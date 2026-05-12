"""Plan Parallelizer — Detect independent steps and schedule concurrent execution.

Uses the DAG structure to find steps that share no dependencies
and can safely run in parallel.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Set

from edac.plan.dag import PlanDAG, Step, StepStatus

logger = logging.getLogger("edac.plan.parallelizer")


class Parallelizer:
    """Analyzes plan DAG to find parallel execution opportunities."""

    def __init__(self, plan: PlanDAG):
        self.plan = plan

    def ready_groups(self) -> List[Set[str]]:
        """Return sets of steps that can execute in parallel now.

        Each set contains steps whose dependencies are all satisfied
        and which are not yet completed.
        """
        completed = {s.id for s in self.plan.list_steps() if s.status == StepStatus.COMPLETED}
        ready = [
            s
            for s in self.plan.list_steps()
            if s.status == StepStatus.PENDING and all(d in completed for d in s.dependencies)
        ]
        # Group by independence: two steps are independent if neither depends on the other
        groups: List[Set[str]] = []
        assigned: Set[str] = set()

        for step in ready:
            if step.id in assigned:
                continue
            group = {step.id}
            assigned.add(step.id)
            for other in ready:
                if other.id in assigned:
                    continue
                if self._independent(step.id, other.id):
                    group.add(other.id)
                    assigned.add(other.id)
            groups.append(group)

        return groups

    def _independent(self, a: str, b: str) -> bool:
        """True if neither step depends (directly or transitively) on the other."""
        return not self._depends_on(a, b) and not self._depends_on(b, a)

    def _depends_on(self, step_id: str, target_id: str) -> bool:
        """DFS check if step_id transitively depends on target_id."""
        visited: Set[str] = set()
        stack = [step_id]
        while stack:
            current = stack.pop()
            if current == target_id and current != step_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            step = self.plan.get_step(current)
            if step:
                stack.extend(step.dependencies)
        return False

    def max_parallelism(self) -> int:
        """Maximum number of steps that can run concurrently at any point."""
        groups = self.plan.parallel_groups()
        if not groups:
            return 0
        return max(len(g) for g in groups)

    def critical_path(self) -> List[str]:
        """Longest dependency chain (critical path) in the plan.

        Steps on the critical path cannot be parallelized.
        """
        memo: Dict[str, int] = {}

        def path_length(step_id: str) -> int:
            if step_id in memo:
                return memo[step_id]
            step = self.plan.get_step(step_id)
            if not step or not step.dependencies:
                memo[step_id] = 1
                return 1
            memo[step_id] = 1 + max(path_length(d) for d in step.dependencies)
            return memo[step_id]

        for sid in self.plan._steps:
            path_length(sid)

        if not memo:
            return []

        # Reconstruct longest path
        start = max(memo, key=memo.get)
        path: List[str] = []
        current = start
        while current:
            path.append(current)
            step = self.plan.get_step(current)
            if not step or not step.dependencies:
                break
            current = max(step.dependencies, key=lambda d: memo.get(d, 0))

        return list(reversed(path))

    def stats(self) -> Dict[str, Any]:
        groups = self.plan.parallel_groups()
        return {
            "total_steps": len(self.plan._steps),
            "parallel_layers": len(groups),
            "max_parallelism": self.max_parallelism(),
            "critical_path_length": len(self.critical_path()),
            "critical_path": self.critical_path(),
        }
