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
        @workflow([
            {"agent": "planner", "task": "plan"},
            {"agent": "coder", "task": "code"},
        ])
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
            .sandbox(True)
            .max_restarts(5)
            .build()
        )
        assert config.name == "coder"
        assert config.model == "claude-sonnet"
        assert config.skills == ["python"]
        assert config.config["sandbox"] is True
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

        bus = EventBus()
        runtime = AgentRuntime(bus)

        wf = Workflow([
            {"agent": "planner", "task": "plan"},
            {"agent": "coder", "task": "code"},
        ])

        from edac.sdk.workflow import WorkflowRunner
        runner = WorkflowRunner(runtime, wf)
        # Without real agents registered, this raises ValueError
        with pytest.raises(ValueError):
            await runner.run()
