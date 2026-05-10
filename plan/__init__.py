"""Plan engine for EDAC.

Mutable DAG plans with dynamic replanning, dependency resolution,
parallel execution detection, and step-level observability.
"""

from edac.plan.dag import PlanDAG, Step, StepStatus
from edac.plan.mutator import PlanMutator
from edac.plan.parallelizer import Parallelizer
from edac.plan.engine import PlanEngine, PlanConfig, ReplanningTrigger

__all__ = [
    "PlanDAG",
    "Step",
    "StepStatus",
    "PlanMutator",
    "Parallelizer",
    "PlanEngine",
    "PlanConfig",
    "ReplanningTrigger",
]
