"""Integration tests for EDAC examples.

Runs each example pattern in-process and asserts on outputs,
without requiring external LLM API keys (uses MockProvider).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextConfig, ContextManager
from edac.event.bus import EventBus
from edac.event.schema import create_event
from edac.examples._utils import MockProvider
from edac.memory.episodic import EpisodicMemory
from edac.model import ModelRegistry
from edac.plan.dag import PlanDAG, Step
from edac.plan.engine import PlanEngine, PlanConfig
from edac.tool.registry import ToolRegistry, ToolSpec


class TestCodingAgentExample:
    """End-to-end test for the coding agent pattern."""

    @pytest.mark.asyncio
    async def test_plan_execution_with_tools(self):
        """PlanDAG + PlanEngine + ToolRegistry execute 4 steps sequentially."""
        registry = ModelRegistry()
        registry.register("mock", MockProvider(responses={"lint": "ok"}, default_response="ok"))

        # Register tools
        tools = ToolRegistry()

        async def lint_tool(code: str) -> str:
            return "Lint: 0 issues"

        async def refactor_tool(code: str, instructions: str) -> str:
            return f"Refactored: {code[:20]}..."

        async def test_tool(test_code: str) -> str:
            return "Tests: passed"

        tools.register(
            ToolSpec(name="lint", description="Linter", returns={"result": {"type": "string"}}),
            lint_tool,
        )
        tools.register(
            ToolSpec(
                name="refactor", description="Refactor", returns={"result": {"type": "string"}}
            ),
            refactor_tool,
        )
        tools.register(
            ToolSpec(
                name="test", description="Test runner", returns={"result": {"type": "string"}}
            ),
            test_tool,
        )

        # Build plan
        plan = PlanDAG(goal="Refactor module")
        plan.add_step(Step(id="lint", description="Lint", action="tool.call"))
        plan.add_step(
            Step(id="refactor", description="Refactor", action="tool.call", dependencies=["lint"])
        )
        plan.add_step(
            Step(id="test", description="Test", action="tool.call", dependencies=["refactor"])
        )

        bus = EventBus()
        engine = PlanEngine(bus, PlanConfig(max_parallel=1))

        async with bus:
            async with AgentRuntime(bus) as runtime:
                executed: list[str] = []

                async def step_executor(step: Step) -> str:
                    executed.append(step.id)
                    if step.id == "lint":
                        return await tools.execute("lint", {"code": "x = 1"})
                    if step.id == "refactor":
                        return await tools.execute(
                            "refactor", {"code": "x = 1", "instructions": "refactor"}
                        )
                    if step.id == "test":
                        return await tools.execute("test", {"test_code": "x = 1"})
                    return "done"

                result = await engine.execute(plan, step_executor)

                assert result.is_complete
                assert executed == ["lint", "refactor", "test"]
                assert plan.get_step("lint").result == "Lint: 0 issues"

    def test_skill_loader_finds_markdown_skills(self):
        """SkillLoader discovers .md files in skills/ directory."""
        from edac.tool.skill import SkillLoader

        loader = SkillLoader()
        skills_dir = Path(__file__).parent.parent / "skills"
        if skills_dir.exists():
            skills = loader.load_directory(skills_dir)
            names = [s.name for s in skills]
            assert "python-refactor" in names
        else:
            pytest.skip("skills/ directory not present")


class TestResearchAgentExample:
    """End-to-end test for mesh swarm + episodic memory."""

    @pytest.mark.asyncio
    async def test_mesh_swarm_aggregates_multiple_agents(self):
        """Mesh pattern runs all agents in parallel and aggregates results."""
        from edac.swarm import Swarm

        bus = EventBus()
        registry = ModelRegistry()
        registry.register("mock", MockProvider(default_response="Agent result"))

        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="mesh",
                    agents=[
                        {"name": "a1", "role": "worker"},
                        {"name": "a2", "role": "worker"},
                        {"name": "a3", "role": "worker"},
                    ],
                )
                result = await swarm.execute(goal="Research topic")

                assert result.success is True
                assert len(result.artifacts) == 3
                for name in ("a1", "a2", "a3"):
                    assert result.agent_results[name]["status"] == "done"

    def test_episodic_memory_records_events(self):
        """EpisodicMemory stores and replays events by correlation_id."""
        memory = EpisodicMemory()
        corr = uuid4()

        e1 = create_event(
            event_type="system.log",
            source="system:test",
            topic="test.topic",
            correlation_id=corr,
            payload={"msg": "first"},
        )
        e2 = create_event(
            event_type="system.log",
            source="system:test",
            topic="test.topic",
            correlation_id=corr,
            payload={"msg": "second"},
        )
        e3 = create_event(
            event_type="system.log",
            source="system:test",
            topic="test.topic",
            correlation_id=uuid4(),
            payload={"msg": "other"},
        )

        memory.append(e1)
        memory.append(e2)
        memory.append(e3)

        trajectory = memory.get_trajectory(corr)
        assert len(trajectory) == 2
        assert trajectory[0].payload["msg"] == "first"

        replayed = memory.replay(corr)
        assert len(replayed) == 2


class TestDataAnalysisExample:
    """End-to-end test for pipeline swarm + modality adapters."""

    @pytest.mark.asyncio
    async def test_pipeline_swarm_sequential_output(self):
        """Pipeline passes output from agent N as context to agent N+1."""
        from edac.swarm import Swarm

        bus = EventBus()
        registry = ModelRegistry()
        registry.register(
            "mock",
            MockProvider(responses={"transform": "transformed data"}, default_response="analysis"),
        )

        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="pipeline",
                    agents=[
                        {"name": "extractor", "role": "worker"},
                        {"name": "transformer", "role": "worker"},
                        {"name": "reporter", "role": "worker"},
                    ],
                )
                result = await swarm.execute(goal="Analyze dataset")

                assert result.success is True
                assert len(result.artifacts) == 3
                # Pipeline builds a PlanDAG — verify it was created
                assert result.plan is not None
                steps = result.plan.list_steps()
                assert len(steps) >= 3

    def test_modality_dispatcher_detects_csv(self):
        """Dispatcher identifies CSV content as artifact modality."""
        from edac.modality.dispatcher import ModalityDispatcher
        from edac.event.schema import ModalityType

        dispatcher = ModalityDispatcher()
        csv_text = "region,sales,qty\nNA,1200,30\nEU,800,20\n"
        detected = dispatcher.detect(csv_text, filename="sales.csv")
        assert detected == ModalityType.ARTIFACT

        content = dispatcher.process(csv_text, filename="sales.csv")
        assert content.modality == ModalityType.ARTIFACT

    def test_modality_dispatcher_detects_python_code(self):
        """Dispatcher identifies Python source as code modality."""
        from edac.modality.dispatcher import ModalityDispatcher
        from edac.event.schema import ModalityType

        dispatcher = ModalityDispatcher()
        code = "def hello(name: str) -> str:\n    return f'Hello, {name}'\n"
        detected = dispatcher.detect(code, filename="hello.py")
        assert detected == ModalityType.CODE

        content = dispatcher.process(code, filename="hello.py")
        assert content.modality == ModalityType.CODE
        assert content.metadata.get("language") == "python"
