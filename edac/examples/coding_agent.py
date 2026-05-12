"""Example 1 — Coding Agent with Plan Engine + Tool Registry + Skill Loader.

Demonstrates full-stack integration:
- EventBus for inter-agent communication
- PlanDAG with dependency tracking and replanning
- ToolRegistry with custom tools (lint, test, refactor)
- SkillLoader for markdown-based skills
- AgentExecutor with real LLM calls (MockProvider for demo)
- Observability (tracing + metrics)

Goal: Refactor a Python file — plan steps, execute tools, review results.

Usage:
    PYTHONPATH=/Users/philosopher/Documents python3 edac/examples/coding_agent.py
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict

from edac import EventBus
from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextConfig, ContextManager
from edac.event.schema import Event, EventType
from edac.observability.metrics import MetricsCollector
from edac.observability.tracing import Tracer
from edac.plan.dag import PlanDAG, Step, StepStatus
from edac.plan.engine import PlanConfig, PlanEngine, ReplanningTrigger
from edac.server.executor import AgentExecutor
from edac.tool.registry import ToolRegistry, ToolSpec
from edac.tool.skill import SkillLoader

from edac.examples._utils import make_registry_with_mock

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("edac.examples.coding")


# ── Custom Tools ──────────────────────────────────────────────


async def lint_tool(code: str, **kwargs: Any) -> str:
    """Simulated linter — checks for type hints and docstrings."""
    issues = []
    if "def " in code and "->" not in code:
        issues.append("Missing return type hints")
    if '"""' not in code and "'''" not in code:
        issues.append("Missing docstrings")
    return "PASS" if not issues else "; ".join(issues)


async def test_tool(test_code: str, **kwargs: Any) -> str:
    """Simulated test runner — checks assertions."""
    if "assert" in test_code:
        return "PASS (3/3 assertions)"
    return "FAIL (no assertions found)"


async def refactor_tool(code: str, instructions: str = "", **kwargs: Any) -> str:
    """Simulated refactor — adds type hints and docstrings."""
    lines = code.splitlines()
    result = []
    for line in lines:
        if line.strip().startswith("def ") and "->" not in line and ":" in line:
            line = line.rstrip(":") + " -> None:"
        result.append(line)
    return "\n".join(result)


# ── Setup ─────────────────────────────────────────────────────

SOURCE_CODE = """
def greet(name):
    return f"Hello, {name}!"

def add(a, b):
    return a + b
"""


def setup_tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="lint",
            description="Run linter on Python code",
            parameters={"code": {"type": "string"}},
            returns={"result": {"type": "string"}},
            category="quality",
        ),
        lint_tool,
    )
    registry.register(
        ToolSpec(
            name="test",
            description="Run tests on Python code",
            parameters={"test_code": {"type": "string"}},
            returns={"result": {"type": "string"}},
            category="quality",
        ),
        test_tool,
    )
    registry.register(
        ToolSpec(
            name="refactor",
            description="Refactor Python code",
            parameters={"code": {"type": "string"}, "instructions": {"type": "string"}},
            returns={"result": {"type": "string"}},
            category="code",
        ),
        refactor_tool,
    )
    return registry


def setup_plan() -> PlanDAG:
    plan = PlanDAG(goal="Refactor Python module for type safety and docs")
    plan.add_step(Step(id="lint", description="Run linter on source", action="tool.call"))
    plan.add_step(
        Step(
            id="refactor",
            description="Apply automated refactoring",
            action="tool.call",
            dependencies=["lint"],
        )
    )
    plan.add_step(
        Step(
            id="test",
            description="Verify with tests",
            action="tool.call",
            dependencies=["refactor"],
        )
    )
    plan.add_step(
        Step(
            id="review",
            description="Human review gate",
            action="human.review",
            dependencies=["test"],
        )
    )
    return plan


def setup_mock_llm() -> Any:
    return make_registry_with_mock(
        responses={
            "refactor": "I've added type hints and docstrings to all functions. The code now passes lint and test checks.",
            "lint": "Lint analysis complete. Found 2 missing type hints and 2 missing docstrings.",
            "review": "Code review: Good refactoring. Type hints are correct. Docstrings follow Google style.",
        },
        default_response="Step completed successfully.",
    )


# ── Main ──────────────────────────────────────────────────────


async def main() -> None:
    # Observability
    tracer = Tracer()
    metrics = MetricsCollector()

    # Model registry (mock for demo)
    model_registry = setup_mock_llm()

    # Context manager with compression
    ctx_config = ContextConfig(
        default_provider="mock",
        max_tokens_per_agent=8000,
        compression_enabled=True,
        compression_trigger_tokens=6000,
    )

    async with EventBus() as bus:
        async with AgentRuntime(bus) as runtime:
            ctx = ContextManager(registry=model_registry, config=ctx_config)
            tool_registry = setup_tools()

            # Load skills
            skill_loader = SkillLoader()
            skills_dir = Path(__file__).parent.parent.parent / "skills"
            if skills_dir.exists():
                skill_loader.load_directory(skills_dir)
                logger.info(f"Loaded {len(skill_loader.list_skills())} skills from {skills_dir}")
            else:
                logger.warning(f"Skills directory not found: {skills_dir}")

            # Create executor
            executor = AgentExecutor(
                bus=bus,
                runtime=runtime,
                registry=model_registry,
                ctx_manager=ctx,
                tools=tool_registry,
                tracer=tracer,
            )

            # Create plan
            plan = setup_plan()
            engine = PlanEngine(
                bus=bus,
                config=PlanConfig(
                    max_parallel=2,
                    max_replans=1,
                    replan_triggers={ReplanningTrigger.STEP_FAILURE},
                ),
            )

            # Execute plan with tool-based step executor
            logger.info("=== Starting Coding Agent ===")
            logger.info(f"Goal: {plan.goal}")
            logger.info(f"Steps: {len(plan.list_steps())}")

            async def step_executor(step: Step) -> str:
                global SOURCE_CODE  # module-level mutable state
                logger.info(f"Executing step: {step.id} — {step.description}")
                metrics.counter("steps_executed", labels={"step_id": step.id}).inc()

                if step.id == "lint":
                    result = await tool_registry.execute("lint", {"code": SOURCE_CODE})
                elif step.id == "refactor":
                    result = await tool_registry.execute(
                        "refactor",
                        {"code": SOURCE_CODE, "instructions": "Add type hints and docstrings"},
                    )
                    # Update source for next steps
                    SOURCE_CODE = result
                elif step.id == "test":
                    result = await tool_registry.execute("test", {"test_code": SOURCE_CODE})
                elif step.id == "review":
                    # Simulate review via LLM
                    result = await ctx.chat(
                        agent_id="reviewer",
                        prompt=f"Review this refactored code:\n{SOURCE_CODE}",
                        provider="mock",
                    )
                else:
                    result = "Unknown step"

                logger.info(f"Step {step.id} result: {result}")
                return result

            completed_plan = await engine.execute(plan, step_executor)

            # Results
            logger.info("=== Results ===")
            for step in completed_plan.list_steps():
                status_icon = "✓" if step.status == StepStatus.COMPLETED else "✗"
                logger.info(f"  {status_icon} {step.id}: {step.status.value} — {step.result}")

            logger.info(f"Completion: {completed_plan.completion_pct * 100:.0f}%")

            # Observability export
            spans = tracer.export()
            logger.info(f"Traces: {len(spans)} spans")
            print("\n--- Metrics ---")
            print(metrics.export())

            return completed_plan


if __name__ == "__main__":
    asyncio.run(main())
