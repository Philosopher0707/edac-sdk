"""Swarm — Multi-agent orchestration.

Patterns:
- pipeline: sequential stages, output of stage N → input of stage N+1
- mesh: all agents broadcast, any can respond
- orchestrator-workers: one agent plans, workers execute in parallel

Usage:
    swarm = Swarm(bus, runtime, pattern="pipeline", agents=[
        {"name": "planner", "role": "planner"},
        {"name": "coder", "role": "worker"},
        {"name": "reviewer", "role": "critic"},
    ])
    result = await swarm.execute(goal="Build a REST API")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from edac.agent.lifecycle import AgentConfig
from edac.agent.runtime import AgentRuntime
from edac.event.bus import EventBus
from edac.event.schema import Event, EventType, create_event
from edac.plan.dag import PlanDAG, Step, StepStatus
from edac.plan.engine import PlanEngine

logger = logging.getLogger("edac.swarm")


@dataclass
class SwarmResult:
    """Result of a swarm execution."""
    success: bool
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    agent_results: Dict[str, Any] = field(default_factory=dict)
    plan: Optional[PlanDAG] = None


class Swarm:
    """Multi-agent orchestrator."""

    def __init__(
        self,
        bus: EventBus,
        runtime: AgentRuntime,
        agents: List[Dict[str, Any]],
        pattern: str = "pipeline",
        coordination: str = "orchestrator-workers",
        max_parallel: int = 3,
        human_approval_on: str = "none",
        tools: Optional[Any] = None,
        guardrail: Optional[Any] = None,
        secrets: Optional[Any] = None,
        tracer: Optional[Any] = None,
        plan_engine: Optional[PlanEngine] = None,
    ):
        self.bus = bus
        self.runtime = runtime
        self.agents = agents
        self.pattern = pattern
        self.coordination = coordination
        self.max_parallel = max_parallel
        self.human_approval_on = human_approval_on
        self.tools = tools
        self.guardrail = guardrail
        self.secrets = secrets
        self.tracer = tracer
        self.plan_engine = plan_engine
        self._results: Dict[str, Any] = {}
        self._events: List[Event] = []

    async def execute(self, goal: str, **kwargs: Any) -> SwarmResult:
        """Execute the swarm on a goal."""
        if self.tracer is not None:
            async with self.tracer.async_span(f"swarm.execute:{self.pattern}") as span:
                span.set_attribute("goal", goal)
                span.set_attribute("agent_count", len(self.agents))
                span.set_attribute("pattern", self.pattern)
                return await self._execute(goal, **kwargs)
        return await self._execute(goal, **kwargs)

    async def _execute(self, goal: str, **kwargs: Any) -> SwarmResult:
        if self.pattern == "pipeline":
            return await self._execute_pipeline(goal, **kwargs)
        if self.pattern == "mesh":
            return await self._execute_mesh(goal, **kwargs)
        if self.pattern == "orchestrator-workers":
            return await self._execute_orchestrator_workers(goal, **kwargs)
        raise ValueError(f"Unknown swarm pattern: {self.pattern}")

    # ── Pipeline ──

    async def _execute_pipeline(self, goal: str, **kwargs: Any) -> SwarmResult:
        """Sequential: each agent's output → next agent's input."""
        logger.info(f"Pipeline swarm executing: {goal}")
        plan = PlanDAG()
        prev_id: Optional[str] = None

        for i, cfg in enumerate(self.agents):
            step_id = f"step-{i}"
            plan.add_step(
                Step(
                    id=step_id,
                    description=f"{cfg['name']}: {cfg.get('task', goal)}",
                    action=cfg.get("role", "execute"),
                    dependencies=[prev_id] if prev_id else [],
                )
            )
            prev_id = step_id

        # Spawn agents
        agent_map: Dict[str, str] = {}
        for cfg in self.agents:
            config = AgentConfig(
                name=cfg["name"],
                agent_type=cfg.get("role", "worker"),
                goal=cfg.get("task", goal),
                model=cfg.get("model"),
                skills=cfg.get("skills", []),
            )
            inst = await self.runtime.spawn(config)
            agent_map[cfg["name"]] = inst.agent_id

        context: Dict[str, Any] = {"goal": goal, **kwargs}

        # Build step_id -> agent_cfg mapping for the executor
        step_agent_map: Dict[str, Dict[str, Any]] = {
            f"step-{i}": cfg for i, cfg in enumerate(self.agents)
        }

        async def step_executor(step: Step) -> Any:
            """Execute a single plan step by invoking the corresponding agent."""
            cfg = step_agent_map[step.id]
            return await self._invoke_agent(cfg["name"], agent_map[cfg["name"]], context)

        engine = self.plan_engine or PlanEngine(self.bus)
        executed_plan = await engine.execute(plan, step_executor)

        # Convert plan results back into SwarmResult shape
        for i, cfg in enumerate(self.agents):
            step_id = f"step-{i}"
            step = executed_plan.get_step(step_id)
            if step and step.result is not None:
                self._results[cfg["name"]] = step.result
                if isinstance(step.result, dict) and "previous_result" in step.result:
                    context = step.result
            else:
                self._results[cfg["name"]] = {"agent": cfg["name"], "status": "done"}

        return SwarmResult(
            success=not any(
                isinstance(r, dict) and r.get("error")
                for r in self._results.values()
            ),
            artifacts=[{"agent": k, "output": v} for k, v in self._results.items()],
            plan=executed_plan,
            agent_results=self._results,
        )

    # ── Mesh ──

    async def _execute_mesh(self, goal: str, **kwargs: Any) -> SwarmResult:
        """All agents receive same input, results aggregated."""
        logger.info(f"Mesh swarm executing: {goal}")

        agent_map: Dict[str, str] = {}
        for cfg in self.agents:
            config = AgentConfig(name=cfg["name"], agent_type=cfg.get("role", "worker"))
            inst = await self.runtime.spawn(config)
            agent_map[cfg["name"]] = inst.agent_id

        context = {"goal": goal, **kwargs}

        # Invoke all in parallel
        coros = [self._invoke_agent(name, agent_map[name], context) for name in agent_map]
        results = await asyncio.gather(*coros, return_exceptions=True)

        for cfg, result in zip(self.agents, results):
            if isinstance(result, Exception):
                self._results[cfg["name"]] = {"error": str(result)}
            else:
                self._results[cfg["name"]] = result

        return SwarmResult(
            success=not any(isinstance(r, Exception) for r in results),
            artifacts=[{"agent": k, "output": v} for k, v in self._results.items()],
            agent_results=self._results,
        )

    # ── Orchestrator-Workers ──

    async def _execute_orchestrator_workers(self, goal: str, **kwargs: Any) -> SwarmResult:
        """Orchestrator plans, workers execute in parallel."""
        logger.info(f"Orchestrator-workers swarm executing: {goal}")

        orchestrator = next((a for a in self.agents if a.get("role") == "orchestrator"), None)
        workers = [a for a in self.agents if a.get("role") != "orchestrator"]

        if orchestrator is None:
            raise ValueError("orchestrator-workers pattern requires an agent with role='orchestrator'")

        # Spawn orchestrator
        orch_config = AgentConfig(
            name=orchestrator["name"],
            agent_type="orchestrator",
            goal=f"Plan execution for: {goal}",
        )
        orch_inst = await self.runtime.spawn(orch_config)

        # Step 1: Orchestrator creates plan
        plan_result = await self._invoke_agent(
            orchestrator["name"], orch_inst.agent_id, {"goal": goal, **kwargs}
        )
        subtasks = plan_result.get("subtasks", [])

        # Step 2: Spawn workers and execute subtasks in parallel (up to max_parallel)
        worker_map: Dict[str, str] = {}
        for cfg in workers:
            wconfig = AgentConfig(
                name=cfg["name"],
                agent_type=cfg.get("role", "worker"),
                goal=cfg.get("task", "execute subtask"),
            )
            inst = await self.runtime.spawn(wconfig)
            worker_map[cfg["name"]] = inst.agent_id

        sem = asyncio.Semaphore(self.max_parallel)

        async def _run_subtask(subtask: Dict[str, Any]) -> Tuple[str, Any]:
            async with sem:
                worker_name = subtask.get("agent", workers[0]["name"] if workers else "worker")
                if worker_name not in worker_map:
                    worker_name = list(worker_map.keys())[0]
                result = await self._invoke_agent(
                    worker_name, worker_map[worker_name], {"subtask": subtask, "goal": goal}
                )
                return worker_name, result

        if subtasks:
            results = await asyncio.gather(*[_run_subtask(st) for st in subtasks], return_exceptions=True)
            for subtask, result in zip(subtasks, results):
                if isinstance(result, Exception):
                    self._results[subtask.get("id", "unknown")] = {"error": str(result)}
                else:
                    self._results[subtask.get("id", result[0])] = result[1]
        else:
            # No subtasks — workers execute directly on goal
            coros = [self._invoke_agent(name, aid, {"goal": goal}) for name, aid in worker_map.items()]
            results = await asyncio.gather(*coros, return_exceptions=True)
            for cfg, result in zip(workers, results):
                if isinstance(result, Exception):
                    self._results[cfg["name"]] = {"error": str(result)}
                else:
                    self._results[cfg["name"]] = result

        return SwarmResult(
            success=not any(isinstance(r, Exception) for r in [r for r in self._results.values()]),
            artifacts=[{"agent": k, "output": v} for k, v in self._results.items()],
            agent_results=self._results,
        )

    # ── Internal ──

    async def _invoke_agent(self, name: str, agent_id: str, context: Dict[str, Any]) -> Any:
        """Simulate agent invocation. Returns agent result."""
        if self.tracer is not None:
            async with self.tracer.async_span(f"agent.invoke:{name}") as span:
                span.set_attribute("agent_name", name)
                span.set_attribute("agent_id", agent_id)
                span.set_attribute("context_keys", list(context.keys()))
                logger.debug(f"Invoking agent {name} ({agent_id}) with context keys: {list(context.keys())}")
                result = await self._do_invoke_agent(name, agent_id, context)
                span.set_attribute("status", result.get("status", "unknown") if isinstance(result, dict) else "done")
                return result
        logger.debug(f"Invoking agent {name} ({agent_id}) with context keys: {list(context.keys())}")
        return await self._do_invoke_agent(name, agent_id, context)

    async def _do_invoke_agent(self, name: str, agent_id: str, context: Dict[str, Any]) -> Any:
        # Placeholder: real implementation would send event, await response
        return {"agent": name, "context_keys": list(context.keys()), "status": "done"}
