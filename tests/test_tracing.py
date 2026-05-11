"""Tests for OpenTelemetry-compatible tracing integration."""

import pytest

from edac.agent.lifecycle import AgentConfig
from edac.agent.runtime import AgentRuntime
from edac.event.bus import EventBus
from edac.observability.tracing import Tracer, Span
from edac.swarm import Swarm
from edac.server.executor import AgentExecutor
from edac.context.manager import ContextManager, ContextConfig
from edac.model import ModelRegistry


class TestTracer:
    def test_start_and_end_span(self):
        tracer = Tracer()
        span = tracer.start_span("test")
        assert isinstance(span, Span)
        assert span.end_time is None
        tracer.end_span(span)
        assert span.end_time is not None

    def test_export(self):
        tracer = Tracer()
        span = tracer.start_span("test")
        tracer.end_span(span)
        exported = tracer.export()
        assert len(exported) == 1
        assert exported[0]["name"] == "test"
        assert exported[0]["duration_ms"] >= 0

    def test_context_manager(self):
        tracer = Tracer()
        with tracer.span("ctx") as span:
            assert span.end_time is None
        assert span.end_time is not None
        assert len(tracer.export()) == 1

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        tracer = Tracer()
        async with tracer.async_span("async_ctx") as span:
            assert span.end_time is None
        assert span.end_time is not None
        assert len(tracer.export()) == 1

    def test_trace_event(self):
        import uuid
        from edac.event.schema import Event, EventType

        tracer = Tracer()
        event = Event(
            event_type=EventType.SYSTEM_LOG,
            source="agent:test-1",
            topic="t1",
            payload={},
            correlation_id=str(uuid.uuid4()),
        )
        span = tracer.trace_event(event)
        tracer.end_span(span)
        exported = tracer.export()
        assert len(exported) == 1
        assert exported[0]["name"] == "event:system.log"

    def test_reset(self):
        tracer = Tracer()
        with tracer.span("s1"):
            pass
        tracer.reset()
        assert len(tracer.export()) == 0
        assert len(tracer.get_finished_spans()) == 0

    def test_parent_span(self):
        tracer = Tracer()
        parent = tracer.start_span("parent")
        child = tracer.start_span("child", parent=parent)
        assert child.parent_id == parent.span_id
        assert child.trace_id == parent.trace_id
        tracer.end_span(child)
        tracer.end_span(parent)

    def test_span_set_attribute(self):
        tracer = Tracer()
        with tracer.span("s") as span:
            span.set_attribute("key", "value")
        exported = tracer.export()
        assert exported[0]["attributes"]["key"] == "value"


class TestSwarmTracing:
    @pytest.mark.asyncio
    async def test_pipeline_creates_swarm_span(self):
        tracer = Tracer()
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="pipeline",
                    agents=[{"name": "a", "role": "worker"}],
                    tracer=tracer,
                )
                result = await swarm.execute(goal="test")
                assert result.success is True

        exported = tracer.export()
        span_names = {s["name"] for s in exported}
        assert "swarm.execute:pipeline" in span_names
        assert "agent.invoke:a" in span_names

    @pytest.mark.asyncio
    async def test_mesh_creates_agent_spans(self):
        tracer = Tracer()
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="mesh",
                    agents=[{"name": "x", "role": "worker"}, {"name": "y", "role": "worker"}],
                    tracer=tracer,
                )
                result = await swarm.execute(goal="test")
                assert result.success is True

        exported = tracer.export()
        span_names = [s["name"] for s in exported]
        assert "swarm.execute:mesh" in span_names
        assert "agent.invoke:x" in span_names
        assert "agent.invoke:y" in span_names

    @pytest.mark.asyncio
    async def test_span_attributes(self):
        tracer = Tracer()
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="pipeline",
                    agents=[{"name": "a", "role": "worker"}],
                    tracer=tracer,
                )
                await swarm.execute(goal="my_goal")

        exported = tracer.export()
        swarm_span = next(s for s in exported if s["name"].startswith("swarm.execute"))
        assert swarm_span["attributes"]["goal"] == "my_goal"
        assert swarm_span["attributes"]["pattern"] == "pipeline"
        assert swarm_span["attributes"]["agent_count"] == 1

        agent_span = next(s for s in exported if s["name"].startswith("agent.invoke"))
        assert agent_span["attributes"]["agent_name"] == "a"
        assert "agent_id" in agent_span["attributes"]
        assert agent_span["attributes"]["context_keys"] == ["goal"]

    @pytest.mark.asyncio
    async def test_no_tracer_no_spans(self):
        """When no tracer is provided, no spans are created."""
        tracer = Tracer()
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="pipeline",
                    agents=[{"name": "a", "role": "worker"}],
                    # no tracer
                )
                result = await swarm.execute(goal="test")
                assert result.success is True

        # tracer should be empty since it was not passed to swarm
        assert len(tracer.export()) == 0


class TestAgentExecutorTracing:
    @pytest.mark.asyncio
    async def test_executor_creates_tracing_spans(self):
        from edac.model import ChatCompletion, ChatMessage, ModelProvider

        class MockProvider(ModelProvider):
            @property
            def name(self):
                return "mock"

            async def is_available(self):
                return True

            async def chat(self, messages, **kwargs):
                return ChatCompletion(content="ok", model="mock")

            async def stream(self, messages, **kwargs):
                pass

            async def close(self):
                pass

        tracer = Tracer()
        registry = ModelRegistry()
        registry.register("mock", MockProvider())
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                ctx = ContextManager(
                    registry=registry,
                    config=ContextConfig(default_provider="mock"),
                )
                executor = AgentExecutor(
                    bus=bus,
                    runtime=runtime,
                    registry=registry,
                    ctx_manager=ctx,
                    tracer=tracer,
                )
                result = await executor.execute(
                    goal="Build API",
                    agents=[{"name": "planner", "role": "worker", "provider": "mock"}],
                    pattern="pipeline",
                )
                assert result.success is True

        exported = tracer.export()
        span_names = {s["name"] for s in exported}
        # Swarm + LLM call (executor replaces swarm._invoke_agent, so no agent.invoke)
        assert "swarm.execute:pipeline" in span_names
        assert "executor.execute" in span_names
        assert "llm.call:planner" in span_names

    @pytest.mark.asyncio
    async def test_executor_span_attributes(self):
        from edac.model import ChatCompletion, ChatMessage, ModelProvider

        class MockProvider(ModelProvider):
            @property
            def name(self):
                return "mock"

            async def is_available(self):
                return True

            async def chat(self, messages, **kwargs):
                return ChatCompletion(content="ok", model="mock")

            async def stream(self, messages, **kwargs):
                pass

            async def close(self):
                pass

        tracer = Tracer()
        registry = ModelRegistry()
        registry.register("mock", MockProvider())
        bus = EventBus()
        async with bus:
            async with AgentRuntime(bus) as runtime:
                ctx = ContextManager(
                    registry=registry,
                    config=ContextConfig(default_provider="mock"),
                )
                executor = AgentExecutor(
                    bus=bus,
                    runtime=runtime,
                    registry=registry,
                    ctx_manager=ctx,
                    tracer=tracer,
                )
                await executor.execute(
                    goal="Build API",
                    agents=[{"name": "planner", "role": "worker", "provider": "mock"}],
                    pattern="pipeline",
                )

        exported = tracer.export()
        exec_span = next(s for s in exported if s["name"] == "executor.execute")
        assert exec_span["attributes"]["goal"] == "Build API"
        assert exec_span["attributes"]["success"] is True

        llm_span = next(s for s in exported if s["name"] == "llm.call:planner")
        assert llm_span["attributes"]["agent_name"] == "planner"
        assert llm_span["attributes"]["provider"] == "mock"
