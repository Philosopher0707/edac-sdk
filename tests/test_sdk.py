"""Tests for SDK decorators and surface."""

import pytest

from edac.sdk.decorators import agent, skill, workflow
from edac.sdk.agent_builder import AgentBuilder
from edac.sdk.skill_builder import SkillBuilder
from edac.sdk.workflow import Workflow


class TestAgentDecorator:
    def test_decorator_attaches_config(self):
        @agent(name="test-agent", model="claude-sonnet")
        async def my_agent(event):
            return event

        assert hasattr(my_agent, "_edac_agent_config")
        assert my_agent._edac_agent_config.name == "test-agent"
        assert my_agent._edac_agent_config.model == "claude-sonnet"

    def test_decorator_default_values(self):
        @agent(name="default-agent")
        async def my_agent(event):
            return event

        cfg = my_agent._edac_agent_config
        assert cfg.model is None
        assert cfg.skills == []
        assert cfg.max_restarts == 3


class TestAgentDecoratorFunctional:
    @pytest.mark.asyncio
    async def test_decorator_config_spawns_in_runtime(self):
        """An @agent decorated function's config should be spawnable in AgentRuntime."""
        from edac.agent.runtime import AgentRuntime
        from edac.event.bus import EventBus

        bus = EventBus()
        await bus.start()
        runtime = AgentRuntime(bus)
        await runtime.start()

        @agent(name="worker", model="test-model", skills=["python"])
        async def worker_agent(event):
            return {"status": "ok"}

        # Use the decorator config to spawn an agent
        cfg = worker_agent._edac_agent_config
        spawned = await runtime.spawn(cfg)
        assert spawned.config.name == "worker"
        assert spawned.config.model == "test-model"
        assert spawned.config.skills == ["python"]
        assert spawned.agent_id.startswith("worker-")

        # Verify it's registered
        assert runtime.registry.find_by_name("worker")

        await runtime.stop()
        await bus.stop()


class TestSkillDecorator:
    def test_inline_skill(self):
        @skill(name="inline-skill", description="test", applies_when="test query")
        async def my_agent(event):
            return event

        assert hasattr(my_agent, "_edac_skill")
        assert my_agent._edac_skill.name == "inline-skill"

    def test_skill_requires_path_or_name(self):
        with pytest.raises(ValueError):

            @skill()
            async def my_agent(event):
                return event


class TestWorkflowDecorator:
    def test_decorator_attaches_workflow(self):
        @workflow(
            [
                {"agent": "planner", "task": "plan"},
                {"agent": "coder", "task": "code"},
            ]
        )
        async def my_pipeline(event):
            return event

        assert hasattr(my_pipeline, "_edac_workflow")
        assert len(my_pipeline._edac_workflow.steps) == 2
        assert my_pipeline._edac_workflow.steps[0]["agent"] == "planner"


class TestAgentBuilder:
    def test_fluent_api(self):
        config = (
            AgentBuilder()
            .name("coder")
            .model("claude-sonnet")
            .skill("python")
            .tool("web_search")
            .memory("short-term")
            .sandbox(True)
            .max_restarts(5)
            .build()
        )
        assert config.name == "coder"
        assert config.model == "claude-sonnet"
        assert config.skills == ["python"]
        assert config.config["sandbox"] is True
        assert config.config["tools"] == ["web_search"]
        assert config.config["memory"] == "short-term"
        assert config.max_restarts == 5


class TestSkillBuilder:
    def test_build_skill(self):
        skill = (
            SkillBuilder()
            .name("test")
            .description("desc")
            .principle("p1")
            .tool("t1")
            .verification("v1")
            .build()
        )
        assert skill.name == "test"
        assert "p1" in skill.body
        assert "t1" in skill.body
        assert "v1" in skill.body


class TestWorkflowRunner:
    @pytest.mark.asyncio
    async def test_run_linear(self):
        from edac.agent.runtime import AgentRuntime
        from edac.event.bus import EventBus
        from edac.agent.lifecycle import AgentConfig

        bus = EventBus()
        await bus.start()
        runtime = AgentRuntime(bus)
        await runtime.start()

        planner = await runtime.spawn(AgentConfig(name="planner"))
        coder = await runtime.spawn(AgentConfig(name="coder"))

        wf = Workflow(
            [
                {"agent": "planner", "task": "plan"},
                {"agent": "coder", "task": "code"},
            ]
        )

        from edac.sdk.workflow import WorkflowRunner

        runner = WorkflowRunner(runtime, wf)
        results = await runner.run()

        assert "agent_id" in results[0]
        assert results[0]["agent_id"] == planner.agent_id
        assert "agent_id" in results[1]
        assert results[1]["agent_id"] == coder.agent_id

        assert len(results) == 2
        assert results[0]["agent"] == "planner"
        assert results[0]["task"] == "plan"
        assert results[1]["agent"] == "coder"
        assert results[1]["task"] == "code"

        await runtime.stop()
        await bus.stop()
