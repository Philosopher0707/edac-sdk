"""Multi-Agent Pipeline — planner → coder → reviewer.

Demonstrates full EDAC stack:
- EventBus for inter-agent communication
- AgentRuntime for lifecycle management
- PlanDAG for execution tracking
- Swarm orchestration with pipeline pattern
- Human-in-the-loop (approval gates)
- Observability (tracing + metrics)

Usage:
    PYTHONPATH=/Users/philosopher/Documents python3 examples/multi_agent_pipeline.py
"""

from __future__ import annotations

import asyncio
import logging

from edac import EventBus, EventType, create_event
from edac.agent.lifecycle import AgentConfig
from edac.agent.runtime import AgentRuntime
from edac.human.approval import ApprovalGate, ApprovalManager
from edac.observability.metrics import MetricsCollector
from edac.observability.tracing import Tracer
from edac.swarm import Swarm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("edac.examples.pipeline")


async def main():
    # ── Setup ──
    tracer = Tracer()
    metrics = MetricsCollector()
    approval_mgr = ApprovalManager()

    # Approval gate for destructive actions
    gate = ApprovalGate(
        trigger_on="tool.git.push",
        prompt="Agent wants to push to main branch. Approve?",
        timeout_seconds=300,
        required_approvers=1,
    )
    approval_mgr.add_gate(gate)

    async with EventBus() as bus:
        async with AgentRuntime(bus) as runtime:
            # ── Swarm Definition ──
            swarm = Swarm(
                bus=bus,
                runtime=runtime,
                pattern="pipeline",
                coordination="orchestrator-workers",
                agents=[
                    {
                        "name": "planner",
                        "role": "orchestrator",
                        "task": "Break goal into subtasks",
                        "model": "claude-opus",
                        "system_prompt": (
                            "You are a senior software architect. Your job is to break a high-level goal "
                            "into concrete, actionable subtasks. Each subtask must have a clear deliverable, "
                            "estimated effort, and assigned role (frontend, backend, database, devops). "
                            "Return a JSON array of subtasks."
                        ),
                    },
                    {
                        "name": "coder",
                        "role": "worker",
                        "task": "Implement subtasks",
                        "model": "claude-sonnet",
                        "skills": ["python-refactor"],
                        "sandbox": True,
                        "system_prompt": (
                            "You are a senior Python engineer. Write clean, type-annotated, testable code. "
                            "Follow PEP 8, use asyncio where appropriate, include docstrings, and handle errors. "
                            "Return only the code files with file paths as comments."
                        ),
                    },
                    {
                        "name": "reviewer",
                        "role": "critic",
                        "task": "Review code quality",
                        "model": "claude-haiku",
                        "system_prompt": (
                            "You are a code reviewer. Check for: type safety, error handling, test coverage, "
                            "naming clarity, PEP 8 compliance, async correctness, and security issues. "
                            "Give a score 1-10 and list specific fixes needed. Be concise."
                        ),
                    },
                ],
                max_parallel=3,
                human_approval_on="destructive",
            )

            # ── Execute ──
            with tracer.span("pipeline_run") as span:
                result = await swarm.execute(
                    goal="Build a REST API for a todo app with FastAPI",
                    constraints=["use async endpoints", "include OpenAPI docs"],
                )

                metrics.counter("pipeline_runs").inc()
                metrics.counter("pipeline_artifacts").inc(len(result.artifacts))

                # ── Results ──
                logger.info(f"Pipeline success: {result.success}")
                logger.info(f"Artifacts: {len(result.artifacts)}")
                for art in result.artifacts:
                    logger.info(f"  - {art['agent']}: {art['output']}")

                # Export traces
                spans = tracer.export()
                logger.info(f"Traces: {len(spans)} spans")

                # Print metrics
                print("\n--- Metrics ---")
                print(metrics.export())

                # Approval state
                print("\n--- Approval Gates ---")
                for g in approval_mgr.gates:
                    print(f"  {g.trigger_on}: approved={g.is_approved()}")

    return result


if __name__ == "__main__":
    asyncio.run(main())
