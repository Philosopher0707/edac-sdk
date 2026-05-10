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

logger = logging.getLogger("edac.server.executor")


class AgentExecutor:
    """Executes multi-agent tasks with real LLM calls."""

    def __init__(
        self,
        bus: EventBus,
        runtime: AgentRuntime,
        registry: ModelRegistry,
        ctx_manager: ContextManager,
        circuit_breakers: Optional[Dict[str, CircuitBreaker]] = None,
    ):
        self.bus = bus
        self.runtime = runtime
        self.registry = registry
        self.ctx = ctx_manager
        self._circuit_breakers = circuit_breakers or {}

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
        )

        # Override _invoke_agent to use real LLM
        original_invoke = swarm._invoke_agent

        async def _real_invoke(name: str, agent_id: str, context: Dict[str, Any]) -> Any:
            context.setdefault("agents", agents)
            return await self._call_llm(name, agent_id, context)

        swarm._invoke_agent = _real_invoke  # type: ignore[method-assign]

        try:
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
        """Call LLM for an agent."""
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

        try:
            cb = self._circuit_breakers.get(provider)
            if cb:
                response = await cb.call(
                    self.ctx.chat,
                    agent_id=agent_id,
                    prompt=prompt,
                    provider=provider,
                    model=model,
                    system_prompt=system_prompt,
                    temperature=0.3,
                )
            else:
                response = await self.ctx.chat(
                    agent_id=agent_id,
                    prompt=prompt,
                    provider=provider,
                    model=model,
                    system_prompt=system_prompt,
                    temperature=0.3,
                )
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

    def _find_agent_config(self, name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Find agent config from context or return defaults."""
        agents = context.get("agents", [])
        for a in agents:
            if a.get("name") == name:
                return a
        return {"name": name}
