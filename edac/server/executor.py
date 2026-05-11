"""Agent Executor — Real LLM-backed agent execution.

Integrates Swarm + ModelRegistry + ContextManager to run agents
with actual language model calls.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from edac.agent.lifecycle import AgentConfig
from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextManager
from edac.event.bus import EventBus
from edac.model import ModelRegistry
from edac.server.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from edac.swarm import Swarm, SwarmResult
from edac.tool.registry import ToolRegistry

logger = logging.getLogger("edac.server.executor")


class SecurityError(Exception):
    """Raised when a prompt fails guardrail screening."""
    pass


class AgentExecutor:
    """Executes multi-agent tasks with real LLM calls."""

    def __init__(
        self,
        bus: EventBus,
        runtime: AgentRuntime,
        registry: ModelRegistry,
        ctx_manager: ContextManager,
        circuit_breakers: Optional[Dict[str, CircuitBreaker]] = None,
        tools: Optional[ToolRegistry] = None,
        guardrail: Optional[Any] = None,
        secrets: Optional[Any] = None,
        tracer: Optional[Any] = None,
        plan_engine: Optional[Any] = None,
    ):
        self.bus = bus
        self.runtime = runtime
        self.registry = registry
        self.ctx = ctx_manager
        self._circuit_breakers = circuit_breakers or {}
        self.tools = tools
        self.guardrail = guardrail
        self.secrets = secrets
        self.tracer = tracer
        self.plan_engine = plan_engine

    async def execute(
        self,
        goal: str,
        agents: List[Dict[str, Any]],
        pattern: str = "pipeline",
        max_parallel: int = 3,
        on_event: Optional[Any] = None,
    ) -> SwarmResult:
        """Execute a multi-agent task with real LLM calls."""
        logger.info(f"Executing task: {goal} with {len(agents)} agents, pattern={pattern}")

        swarm = Swarm(
            bus=self.bus,
            runtime=self.runtime,
            agents=agents,
            pattern=pattern,
            max_parallel=max_parallel,
            tools=self.tools,
            guardrail=self.guardrail,
            secrets=self.secrets,
            tracer=self.tracer,
            plan_engine=self.plan_engine,
        )

        # Override _invoke_agent to use real LLM
        async def _real_invoke(name: str, agent_id: str, context: Dict[str, Any]) -> Any:
            context.setdefault("agents", agents)
            return await self._call_llm(name, agent_id, context)

        swarm._invoke_agent = _real_invoke  # type: ignore[method-assign]

        try:
            if self.tracer is not None:
                async with self.tracer.async_span("executor.execute") as span:
                    span.set_attribute("goal", goal)
                    span.set_attribute("pattern", pattern)
                    span.set_attribute("agent_count", len(agents))
                    result = await swarm.execute(goal)
                    span.set_attribute("success", result.success)
                    return result
            result = await swarm.execute(goal)
            return result
        except Exception as e:
            logger.exception(f"Task execution failed: {e}")
            return SwarmResult(
                success=False,
                artifacts=[],
                agent_results={"error": str(e)},
            )

    async def _call_llm(
        self,
        name: str,
        agent_id: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Call LLM for an agent with optional guardrails, tools, and tracing."""
        agent_cfg = self._find_agent_config(name, context)
        system_prompt = agent_cfg.get("system_prompt", f"You are the {name} agent.")
        model = agent_cfg.get("model")
        provider = agent_cfg.get("provider", "ollama")

        # Build prompt from context
        goal = context.get("goal", "")
        previous = context.get("previous_result", {})
        constraints = context.get("constraints", [])

        prompt_parts = [f"Goal: {goal}"]
        if previous:
            prompt_parts.append(f"Previous result: {json.dumps(previous, indent=2)}")
        if constraints:
            prompt_parts.append(f"Constraints: {', '.join(constraints)}")

        prompt = "\n\n".join(prompt_parts)

        # Guardrail screening
        if self.guardrail is not None:
            if not self.guardrail.check(prompt):
                logger.warning(f"Guardrail blocked prompt for agent {name}")
                return {
                    "agent": name,
                    "error": "Prompt failed guardrail check",
                    "status": "failed",
                }
            if system_prompt and not self.guardrail.check(system_prompt):
                logger.warning(f"Guardrail blocked system prompt for agent {name}")
                return {
                    "agent": name,
                    "error": "System prompt failed guardrail check",
                    "status": "failed",
                }

        try:
            # Raw LLM call (with optional tracing around it)
            if self.tracer is not None:
                async with self.tracer.async_span(f"llm.call:{name}") as span:
                    span.set_attribute("agent_name", name)
                    span.set_attribute("agent_id", agent_id)
                    span.set_attribute("provider", provider)
                    span.set_attribute("model", model or "default")
                    span.set_attribute("prompt_length", len(prompt))
                    response = await self._do_llm_call(
                        agent_id, prompt, system_prompt, provider, model
                    )
                    if isinstance(response, str):
                        span.set_attribute("response_length", len(response))
            else:
                response = await self._do_llm_call(
                    agent_id, prompt, system_prompt, provider, model
                )

            # Optional tool execution loop (ReAct-style)
            if self.tools is not None:
                for iteration in range(10):
                    tool_call = self._extract_tool_call(response)
                    if tool_call is None:
                        break
                    tool_name, tool_args = tool_call
                    try:
                        tool_result = await self.tools.execute(
                            tool_name, tool_args, correlation_id=agent_id
                        )
                    except Exception as te:
                        tool_result = f"Tool error: {te}"
                    # Feed tool result back into context
                    self.ctx.add_to_window(
                        agent_id, "tool", f"Tool {tool_name} result: {tool_result}"
                    )
                    # Re-call LLM with updated context
                    response = await self._do_llm_call(
                        agent_id, prompt, system_prompt, provider, model
                    )
                else:
                    logger.warning(f"Tool loop exceeded max iterations for agent {name}")

            return {
                "agent": name,
                "response": response,
                "status": "done",
                "model": model,
                "provider": provider,
            }
        except CircuitBreakerOpenError as e:
            logger.warning(f"Circuit breaker open for {provider}: {e}")
            return {
                "agent": name,
                "error": f"Circuit breaker open for {provider}",
                "status": "failed",
            }
        except Exception as e:
            logger.error(f"LLM call failed for {name}: {e}")
            return {
                "agent": name,
                "error": str(e),
                "status": "failed",
            }

    async def _do_llm_call(
        self,
        agent_id: str,
        prompt: str,
        system_prompt: str,
        provider: str,
        model: Optional[str],
    ) -> str:
        """Raw LLM call through circuit breaker or direct."""
        cb = self._circuit_breakers.get(provider)
        if cb:
            return await cb.call(
                self.ctx.chat,
                agent_id=agent_id,
                prompt=prompt,
                provider=provider,
                model=model,
                system_prompt=system_prompt,
                temperature=0.3,
            )
        return await self.ctx.chat(
            agent_id=agent_id,
            prompt=prompt,
            provider=provider,
            model=model,
            system_prompt=system_prompt,
            temperature=0.3,
        )

    def _extract_tool_call(self, response: str) -> Optional[tuple[str, Dict[str, Any]]]:
        """Detect a tool-call request in an LLM response.

        Heuristic: looks for JSON with 'tool' or 'function' key.
        Returns (tool_name, arguments) or None if no tool call detected.
        """
        import re
        # Try to find JSON block
        json_match = re.search(r'\{.*["\']tool["\'].*\}', response, re.DOTALL)
        if not json_match:
            json_match = re.search(r'\{.*["\']function["\'].*\}', response, re.DOTALL)
        if not json_match:
            return None
        try:
            data = json.loads(json_match.group())
            tool_name = data.get("tool") or data.get("function") or data.get("name")
            if not tool_name or (self.tools is not None and not self.tools.has(tool_name)):
                return None
            args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"input": args}
            return str(tool_name), args
        except json.JSONDecodeError:
            return None

    def _find_agent_config(self, name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Find agent config from context or return defaults."""
        agents = context.get("agents", [])
        for a in agents:
            if a.get("name") == name:
                return a
        return {"name": name}
