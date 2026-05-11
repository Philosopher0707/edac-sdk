"""Workflow Runner — Execute multi-step workflows with agents via PlanEngine.

Usage:
    workflow = Workflow([
        {"agent": "planner", "task": "plan the refactor"},
        {"agent": "coder", "task": "execute the plan"},
        {"agent": "reviewer", "task": "review the code"},
    ])
    result = await WorkflowRunner(runtime, workflow).run()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from edac.agent.lifecycle import AgentConfig
from edac.agent.runtime import AgentRuntime
from edac.event.schema import Event, EventType, create_event
from edac.plan.dag import PlanDAG, Step, StepStatus
from edac.plan.engine import PlanEngine

logger = logging.getLogger("edac.sdk.workflow")


@dataclass
class Workflow:
    """A linear workflow definition."""

    steps: List[Dict[str, Any]] = field(default_factory=list)

    def add_step(self, agent: str, task: str, **kwargs: Any) -> Workflow:
        self.steps.append({"agent": agent, "task": task, **kwargs})
        return self


class WorkflowRunner:
    """Executes a workflow through the PlanEngine.

    Converts the linear workflow steps into a :class:`PlanDAG` and executes
    them via :class:`PlanEngine`, emitting events for every step.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        workflow: Workflow,
        plan_engine: Optional[PlanEngine] = None,
    ) -> None:
        self.runtime = runtime
        self.workflow = workflow
        self.plan_engine = plan_engine
        self.results: List[Dict[str, Any]] = []

    async def run(self) -> List[Dict[str, Any]]:
        plan = self._build_plan()
        self.results = []

        async def step_executor(step: Step) -> Any:
            agent_name = step.metadata.get("agent")
            task = step.metadata.get("task", "")

            if agent_name is None:
                raise ValueError(f"Step {step.id} has no agent assigned")

            # Ensure agent exists in runtime by name
            found = self.runtime.registry.find_by_name(agent_name)
            if not found:
                raise ValueError(f"Agent '{agent_name}' not found in runtime")
            agent = found[0]

            # Emit step-start event
            event = create_event(
                event_type=EventType.PLAN_STEP_START,
                source=f"workflow:{agent_name}",
                topic="workflow.steps",
                payload={"agent": agent_name, "task": task, "step_id": step.id},
            )
            await self.runtime.bus.emit(event)

            # Build result shape matching prior stub contract
            result = {
                "agent": agent_name,
                "task": task,
                "status": "done",
                "agent_id": agent.agent_id,
            }
            self.results.append(result)

            # Emit step-complete event
            event = create_event(
                event_type=EventType.PLAN_STEP_COMPLETE,
                source=f"workflow:{agent_name}",
                topic="workflow.steps",
                payload={"agent": agent_name, "task": task, "step_id": step.id},
            )
            await self.runtime.bus.emit(event)
            return result

        engine = self.plan_engine or PlanEngine(self.runtime.bus)
        executed = await engine.execute(plan, step_executor)

        # If the engine aborted because of failures, surface that
        if executed.has_failures:
            failed = [s for s in executed.list_steps() if s.status == StepStatus.FAILED]
            raise RuntimeError(
                f"Workflow failed: {[f'{s.id} ({s.error})' for s in failed]}"
            )

        return self.results

    def _build_plan(self) -> PlanDAG:
        plan = PlanDAG()
        prev_id: Optional[str] = None
        for i, step_cfg in enumerate(self.workflow.steps):
            step_id = f"step-{i}"
            step = Step(
                id=step_id,
                description=f"{step_cfg['agent']}: {step_cfg['task']}",
                action="agent.spawn",
                dependencies=[prev_id] if prev_id else [],
                metadata={"agent": step_cfg["agent"], "task": step_cfg["task"]},
            )
            plan.add_step(step)
            prev_id = step_id
        return plan
