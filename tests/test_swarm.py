"""Tests for Swarm multi-agent orchestration."""

import pytest

from edac.agent.lifecycle import AgentConfig
from edac.agent.runtime import AgentRuntime
from edac.event.bus import EventBus
from edac.swarm import Swarm, SwarmResult


class TestSwarmPipeline:
    @pytest.mark.asyncio
    async def test_pipeline_success(self):
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="pipeline",
                    agents=[
                        {"name": "planner", "role": "orchestrator"},
                        {"name": "coder", "role": "worker"},
                        {"name": "reviewer", "role": "critic"},
                    ],
                )
                result = await swarm.execute(goal="Build API")
                assert result.success is True
                assert result.plan is not None
                assert len(result.artifacts) == 3
                assert result.agent_results["planner"]["status"] == "done"
                assert result.agent_results["coder"]["status"] == "done"
                assert result.agent_results["reviewer"]["status"] == "done"

    @pytest.mark.asyncio
    async def test_pipeline_with_kwargs(self):
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="pipeline",
                    agents=[{"name": "a", "role": "worker"}],
                )
                result = await swarm.execute(goal="G", extra="data")
                assert result.success is True
                assert "goal" in result.agent_results["a"]["context_keys"]
                assert "extra" in result.agent_results["a"]["context_keys"]


class TestSwarmMesh:
    @pytest.mark.asyncio
    async def test_mesh_parallel(self):
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="mesh",
                    agents=[
                        {"name": "a", "role": "worker"},
                        {"name": "b", "role": "worker"},
                    ],
                )
                result = await swarm.execute(goal="Analyze")
                assert result.success is True
                assert len(result.artifacts) == 2


class TestSwarmOrchestratorWorkers:
    @pytest.mark.asyncio
    async def test_orchestrator_workers_with_subtasks(self):
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="orchestrator-workers",
                    max_parallel=2,
                    agents=[
                        {"name": "boss", "role": "orchestrator"},
                        {"name": "w1", "role": "worker"},
                        {"name": "w2", "role": "worker"},
                    ],
                )
                result = await swarm.execute(goal="Build app")
                assert result.success is True
                assert len(result.artifacts) >= 2

    @pytest.mark.asyncio
    async def test_missing_orchestrator_raises(self):
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="orchestrator-workers",
                    agents=[{"name": "w1", "role": "worker"}],
                )
                with pytest.raises(ValueError):
                    await swarm.execute(goal="Build app")


class TestSwarmInvalidPattern:
    def test_unknown_pattern_raises(self):
        swarm = Swarm(
            bus=EventBus(),
            runtime=None,  # type: ignore
            agents=[],
            pattern="unknown",
        )
        import asyncio
        with pytest.raises(ValueError):
            asyncio.run(swarm.execute(goal="x"))

    def test_swarm_result_dataclass(self):
        r = SwarmResult(success=True, artifacts=[{"x": 1}])
        assert r.success is True
        assert r.artifacts == [{"x": 1}]
        assert r.events == []
