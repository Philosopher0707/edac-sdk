"""Plan Engine — Execute PlanDAGs with replanning, parallel execution, and event emission.

The engine:
1. Evaluates ready steps from the DAG
2. Executes them (concurrently where safe)
3. Handles failures with replanning triggers
4. Emits events for every state change
5. Supports human approval gates
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

from edac.event.bus import EventBus
from edac.event.schema import Event, EventType, EventPriority, create_event
from edac.plan.dag import PlanDAG, Step, StepStatus
from edac.plan.mutator import PlanMutator
from edac.plan.parallelizer import Parallelizer

logger = logging.getLogger("edac.plan.engine")


class ReplanningTrigger(str, Enum):
    STEP_FAILURE = "step_failure"
    TEST_FAILURE = "test_failure"
    COMPILATION_ERROR = "compilation_error"
    CONFIDENCE_LOW = "confidence_low"
    DIVERGENCE = "divergence"
    NEW_CONSTRAINT = "new_constraint"
    SHORTCUT_FOUND = "shortcut_found"


@dataclass
class PlanConfig:
    """Configuration for plan execution."""
    max_parallel: int = 3
    max_replans: int = 5
    replan_triggers: Set[ReplanningTrigger] = field(default_factory=lambda: {
        ReplanningTrigger.STEP_FAILURE,
    })
    confidence_threshold: float = 0.7
    approval_gates: List[str] = field(default_factory=list)
    timeout_per_step: Optional[float] = 60.0


class PlanEngine:
    """Executes a PlanDAG with event-driven observability and replanning."""

    def __init__(
        self,
        bus: EventBus,
        config: Optional[PlanConfig] = None,
    ):
        self.bus = bus
        self.config = config or PlanConfig()
        self._replan_count = 0

    # ── Execution ──

    async def execute(
        self,
        plan: PlanDAG,
        step_executor: Callable[[Step], Coroutine[Any, Any, Any]],
    ) -> PlanDAG:
        """Execute a plan to completion.

        Args:
            plan: The PlanDAG to execute
            step_executor: Async function that executes a single step

        Returns:
            The executed plan (mutated in place)
        """
        await self._emit_plan_event(plan, EventType.PLAN_CREATE)
        mutator = PlanMutator(plan)
        parallelizer = Parallelizer(plan)

        while not plan.is_complete:
            # Check for failures
            if plan.has_failures:
                if self._should_replan(plan):
                    await self._replan(plan, mutator, reason="step_failure")
                    continue
                else:
                    await self._emit_plan_event(plan, EventType.PLAN_ABORT)
                    return plan

            # Find ready steps
            groups = parallelizer.ready_groups()
            if not groups:
                # No ready steps but plan not complete — deadlock or waiting
                await asyncio.sleep(0.1)
                continue

            # Execute first group (all parallelizable steps)
            ready_ids = groups[0]
            steps = [plan.get_step(sid) for sid in ready_ids if plan.get_step(sid)]
            steps = [s for s in steps if s.status == StepStatus.PENDING]

            if not steps:
                await asyncio.sleep(0.1)
                continue

            # Limit parallelism
            steps = steps[:self.config.max_parallel]

            await self._emit_plan_event(plan, EventType.PLAN_STEP_START, steps=steps)

            # Execute concurrently
            results = await asyncio.gather(
                *[self._run_step(s, step_executor) for s in steps],
                return_exceptions=True,
            )

            for step, result in zip(steps, results):
                if isinstance(result, Exception):
                    mutator.set_step_failed(step.id, str(result))
                    await self._emit_step_event(plan, step, EventType.PLAN_STEP_FAIL, error=str(result))
                else:
                    mutator.set_step_result(step.id, result)
                    await self._emit_step_event(plan, step, EventType.PLAN_STEP_COMPLETE, result=result)

        await self._emit_plan_event(plan, EventType.PLAN_COMPLETE)
        return plan

    async def _run_step(
        self,
        step: Step,
        executor: Callable[[Step], Coroutine[Any, Any, Any]],
    ) -> Any:
        """Execute a single step with timeout and status tracking."""
        await self._emit_step_event(
            None, step, EventType.PLAN_STEP_START,
        )
        step.status = StepStatus.IN_PROGRESS

        try:
            if self.config.timeout_per_step:
                result = await asyncio.wait_for(
                    executor(step),
                    timeout=self.config.timeout_per_step,
                )
            else:
                result = await executor(step)
            return result
        except asyncio.TimeoutError:
            raise TimeoutError(f"Step {step.id} timed out after {self.config.timeout_per_step}s")
        except Exception as e:
            logger.exception(f"Step {step.id} failed: {e}")
            raise

    # ── Replanning ──

    def _should_replan(self, plan: PlanDAG) -> bool:
        if self._replan_count >= self.config.max_replans:
            return False
        if ReplanningTrigger.STEP_FAILURE in self.config.replan_triggers:
            return True
        return False

    async def _replan(
        self,
        plan: PlanDAG,
        mutator: PlanMutator,
        reason: str,
    ) -> None:
        self._replan_count += 1
        await self._emit_plan_event(plan, EventType.PLAN_REPLAN, replan_reason=reason)
        logger.info(f"Replanning ({self._replan_count}/{self.config.max_replans}): {reason}")

        # Default replan: retry failed steps
        for step in plan.list_steps():
            if step.status == StepStatus.FAILED:
                step.status = StepStatus.PENDING
                step.error = None

        # If max replans exceeded, mark remaining failed steps as terminal
        if self._replan_count >= self.config.max_replans:
            for step in plan.list_steps():
                if step.status == StepStatus.FAILED:
                    logger.error(f"Step {step.id} failed permanently after {self._replan_count} replans")

    # ── Event Emission ──

    async def _emit_plan_event(
        self,
        plan: PlanDAG,
        event_type: EventType,
        replan_reason: Optional[str] = None,
        steps: Optional[List[Step]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "plan_version": plan.version,
            "completion_pct": plan.completion_pct,
        }
        if replan_reason:
            payload["replan_reason"] = replan_reason
        if steps:
            payload["step_ids"] = [s.id for s in steps]

        event = create_event(
            event_type=event_type,
            source="system:plan_engine",
            topic=f"plan.events",
            payload=payload,
            priority=EventPriority.HIGH if event_type in (EventType.PLAN_REPLAN, EventType.PLAN_COMPLETE) else EventPriority.NORMAL,
        )
        await self.bus.emit(event)

    async def _emit_step_event(
        self,
        plan: Optional[PlanDAG],
        step: Step,
        event_type: EventType,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "step_id": step.id,
            "step_description": step.description,
            "step_status": step.status.value,
        }
        if result is not None:
            payload["step_result"] = str(result)[:1000]
        if error:
            payload["step_error"] = error

        event = create_event(
            event_type=event_type,
            source="system:plan_engine",
            topic=f"plan.step.{step.id}",
            payload=payload,
            priority=EventPriority.HIGH if event_type in (EventType.PLAN_STEP_FAIL, EventType.PLAN_REPLAN) else EventPriority.NORMAL,
        )
        await self.bus.emit(event)
