"""Plan Mutator — Insert, remove, reorder, and modify plan steps dynamically.

Provides surgical plan editing while preserving DAG invariants:
- No cycles after any mutation
- Dependencies always valid
- Version increments on every change
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from edac.plan.dag import PlanDAG, Step, StepStatus

logger = logging.getLogger("edac.plan.mutator")


class PlanMutator:
    """Mutable operations on a PlanDAG with validation."""

    def __init__(self, plan: PlanDAG):
        self.plan = plan

    # ── Insert ──

    def insert_after(self, anchor_id: str, new_steps: List[Step]) -> None:
        """Insert steps after a specific anchor step.

        New steps depend on the anchor (and inherit its other dependents).
        """
        anchor = self.plan.get_step(anchor_id)
        if anchor is None:
            raise KeyError(f"Anchor step {anchor_id} not found")

        # New steps depend on anchor
        for s in new_steps:
            if anchor_id not in s.dependencies:
                s.dependencies.append(anchor_id)

        # Steps that previously depended only on anchor now also depend on new steps
        # (to preserve execution order)
        new_ids = {s.id for s in new_steps}
        for step in self.plan.list_steps():
            if anchor_id in step.dependencies and not any(
                d != anchor_id for d in step.dependencies
            ):
                step.dependencies.extend(new_ids)

        for s in new_steps:
            self.plan.add_step(s)

        logger.info(f"Inserted {len(new_steps)} step(s) after {anchor_id}")

    def insert_before_next(self, new_steps: List[Step]) -> None:
        """Insert steps before the next unexecuted (pending) step."""
        pending = [s for s in self.plan.list_steps() if s.status == StepStatus.PENDING]
        if not pending:
            # No pending steps — append to end
            for s in new_steps:
                self.plan.add_step(s)
            return

        # Find all ready pending steps (next layer to execute)
        completed = {s.id for s in self.plan.list_steps() if s.status == StepStatus.COMPLETED}
        ready_pending = [s for s in pending if all(d in completed for d in s.dependencies)]
        targets = ready_pending if ready_pending else pending

        new_ids = {s.id for s in new_steps}
        for target in targets:
            for nid in new_ids:
                if nid not in target.dependencies:
                    target.dependencies.append(nid)

        for s in new_steps:
            self.plan.add_step(s)

        logger.info(f"Inserted {len(new_steps)} step(s) before {[t.id for t in targets]}")

    def append(self, new_steps: List[Step]) -> None:
        """Append steps at the end of the plan."""
        for s in new_steps:
            self.plan.add_step(s)

    # ── Remove ──

    def remove_step(self, step_id: str, reason: str = "") -> Optional[Step]:
        """Remove a step and rewire dependents to its dependencies."""
        step = self.plan.get_step(step_id)
        if step is None:
            return None

        # Rewire: dependents inherit step's dependencies
        for s in self.plan.list_steps():
            if step_id in s.dependencies:
                s.dependencies.remove(step_id)
                for dep in step.dependencies:
                    if dep not in s.dependencies:
                        s.dependencies.append(dep)

        removed = self.plan.remove_step(step_id)
        logger.info(f"Removed step {step_id}: {reason}")
        return removed

    # ── Reorder ──

    def reorder_steps(self, new_order: List[str]) -> None:
        """Reorder steps by assigning dependencies to enforce the new order.

        Each step depends on all previous steps in the new order.
        This is a blunt instrument — prefer parallel_groups for parallelism.
        """
        if set(new_order) != set(self.plan._steps.keys()):
            raise ValueError("new_order must contain exactly the same step IDs")

        for i, step_id in enumerate(new_order):
            step = self.plan.get_step(step_id)
            if step is None:
                continue
            # Depends on all previous steps
            step.dependencies = [new_order[j] for j in range(i)]

        self.plan._version += 1
        logger.info(f"Reordered {len(new_order)} steps")

    # ── Update ──

    def update_step(self, step_id: str, **kwargs: Any) -> Step:
        return self.plan.update_step(step_id, **kwargs)

    def set_step_result(self, step_id: str, result: Any) -> None:
        self.plan.update_step(step_id, status=StepStatus.COMPLETED, result=result)

    def set_step_failed(self, step_id: str, error: str) -> None:
        self.plan.update_step(step_id, status=StepStatus.FAILED, error=error)

    def skip_step(self, step_id: str) -> None:
        self.plan.update_step(step_id, status=StepStatus.SKIPPED)

    # ── Bulk ──

    def replace_plan(self, new_plan: PlanDAG) -> None:
        """Replace entire plan contents while keeping the same object identity."""
        self.plan.goal = new_plan.goal
        self.plan._steps = new_plan._steps
        self.plan._version = new_plan._version
        logger.info("Plan replaced entirely")
