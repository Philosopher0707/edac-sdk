"""Plan DAG — Mutable directed acyclic graph of plan steps.

A Plan is a living document: steps can be inserted, removed, reordered,
and executed in parallel where dependencies allow.

Each step:
    id, description, action, dependencies, status, result, error

Dependencies are explicit DAG edges. No cycles allowed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("edac.plan.dag")


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Step:
    """A single step in a plan DAG."""
    id: str
    description: str
    action: str  # e.g. "tool.call", "agent.spawn", "human.approval"
    dependencies: List[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def copy(self) -> Step:
        return Step(
            id=self.id,
            description=self.description,
            action=self.action,
            dependencies=list(self.dependencies),
            status=self.status,
            result=self.result,
            error=self.error,
            metadata=dict(self.metadata),
        )


class PlanDAG:
    """Mutable DAG of plan steps with dependency resolution and cycle detection."""

    def __init__(self, goal: str = "", steps: Optional[List[Step]] = None):
        self.goal = goal
        self._steps: Dict[str, Step] = {}
        self._version = 1
        if steps:
            for s in steps:
                self.add_step(s)

    # ── Step CRUD ──

    def add_step(self, step: Step) -> None:
        if step.id in self._steps:
            raise ValueError(f"Step {step.id} already exists")
        self._validate_deps([step])
        self._steps[step.id] = step
        self._version += 1

    def get_step(self, step_id: str) -> Optional[Step]:
        return self._steps.get(step_id)

    def remove_step(self, step_id: str) -> Optional[Step]:
        step = self._steps.pop(step_id, None)
        if step:
            # Remove this step from other steps' dependencies and rewire
            for s in self._steps.values():
                if step_id in s.dependencies:
                    s.dependencies.remove(step_id)
                    for dep in step.dependencies:
                        if dep not in s.dependencies:
                            s.dependencies.append(dep)
            self._version += 1
        return step

    def update_step(self, step_id: str, **kwargs: Any) -> Step:
        step = self._steps.get(step_id)
        if step is None:
            raise KeyError(f"Step {step_id} not found")
        for k, v in kwargs.items():
            if hasattr(step, k):
                setattr(step, k, v)
        self._version += 1
        return step

    def list_steps(self) -> List[Step]:
        return list(self._steps.values())

    # ── Dependency Graph ──

    def _validate_deps(self, steps: List[Step]) -> None:
        """Ensure no cycles are introduced."""
        # Build adjacency list
        adj: Dict[str, Set[str]] = {s.id: set(s.dependencies) for s in self._steps.values()}
        for s in steps:
            adj[s.id] = set(s.dependencies)

        # DFS cycle detection
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in adj.get(node, set()):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        for node in adj:
            if node not in visited:
                if has_cycle(node):
                    raise ValueError("Cycle detected in plan dependencies")

    # ── Execution Order ──

    def ready_steps(self) -> List[Step]:
        """Steps whose dependencies are all completed."""
        completed = {s.id for s in self._steps.values() if s.status == StepStatus.COMPLETED}
        return [
            s for s in self._steps.values()
            if s.status == StepStatus.PENDING and all(d in completed for d in s.dependencies)
        ]

    def topological_order(self) -> List[str]:
        """Kahn's algorithm for topological sort."""
        in_degree: Dict[str, int] = {s.id: 0 for s in self._steps.values()}
        adj: Dict[str, List[str]] = {s.id: [] for s in self._steps.values()}

        for s in self._steps.values():
            for dep in s.dependencies:
                if dep in adj:
                    adj[dep].append(s.id)
                    in_degree[s.id] += 1

        queue = [n for n, d in in_degree.items() if d == 0]
        order: List[str] = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(self._steps):
            raise ValueError("Cycle detected — cannot produce topological order")

        return order

    # ── Parallel Groups ──

    def parallel_groups(self) -> List[Set[str]]:
        """Group steps into parallelizable layers.

        Each layer contains steps that can run concurrently
        because all their dependencies are in previous layers.
        """
        in_degree: Dict[str, int] = {s.id: 0 for s in self._steps.values()}
        adj: Dict[str, List[str]] = {s.id: [] for s in self._steps.values()}

        for s in self._steps.values():
            for dep in s.dependencies:
                if dep in adj:
                    adj[dep].append(s.id)
                    in_degree[s.id] += 1

        groups: List[Set[str]] = []
        current = {n for n, d in in_degree.items() if d == 0}

        while current:
            groups.append(current)
            next_layer: Set[str] = set()
            for node in current:
                for neighbor in adj[node]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_layer.add(neighbor)
            current = next_layer

        return groups

    # ── Plan State ──

    @property
    def version(self) -> int:
        return self._version

    @property
    def is_complete(self) -> bool:
        return all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in self._steps.values())

    @property
    def has_failures(self) -> bool:
        return any(s.status == StepStatus.FAILED for s in self._steps.values())

    @property
    def completion_pct(self) -> float:
        if not self._steps:
            return 0.0
        done = sum(1 for s in self._steps.values() if s.status != StepStatus.PENDING)
        return done / len(self._steps)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "version": self._version,
            "steps": [s.__dict__ for s in self._steps.values()],
            "complete": self.is_complete,
            "failed": self.has_failures,
            "completion_pct": self.completion_pct,
        }

    def clone(self) -> PlanDAG:
        """Deep copy of the plan DAG."""
        new = PlanDAG(goal=self.goal)
        for s in self._steps.values():
            new.add_step(s.copy())
        return new
