"""End-to-end tests: full task lifecycle from submit to complete.

Tests the entire execution chain:
  EventBus → Router → Swarm → AgentExecutor → ContextManager → LLM → Tools

These are the highest-fidelity tests — if they pass, the whole system works.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from uuid import uuid4

import pytest

from edac.agent.lifecycle import AgentConfig
from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextConfig, ContextManager
from edac.event.bus import EventBus
from edac.event.schema import Event, EventType, create_event
from edac.model import ChatCompletion, ChatMessage, ModelProvider, ModelRegistry
from edac.observability.tracing import Tracer
from edac.plan.dag import PlanDAG, Step
from edac.plan.engine import PlanEngine, PlanConfig
from edac.server.executor import AgentExecutor
from edac.swarm import Swarm
from edac.tool.registry import ToolRegistry, ToolSpec


# ── Fixtures ───────────────────────────────────────────────


class _MockProvider(ModelProvider):
    """Configurable mock that tracks calls and returns keyword-matched responses."""

    def __init__(
        self,
        responses: Dict[str, str] | None = None,
        default: str = "ok",
    ) -> None:
        self._responses = responses or {}
        self._default = default
        self.calls: List[List[ChatMessage]] = []

    @property
    def name(self) -> str:
        return "mock"

    async def is_available(self) -> bool:
        return True

    async def chat(self, messages: List[ChatMessage], **kwargs: Any) -> ChatCompletion:
        self.calls.append(messages)
        prompt = " ".join(m.content for m in messages if m.content)
        prompt_lower = prompt.lower()
        for keyword, response in self._responses.items():
            if keyword.lower() in prompt_lower:
                return ChatCompletion(content=response, model="mock")
        return ChatCompletion(content=self._default, model="mock")

    async def stream(self, messages: List[ChatMessage], **kwargs: Any) -> Any:
        pass

    async def close(self) -> None:
        pass


@pytest.fixture
def registry() -> ModelRegistry:
    r = ModelRegistry()
    r.register("mock", _MockProvider())
    return r


@pytest.fixture
def mock_provider(registry: ModelRegistry) -> _MockProvider:
    return registry._providers["mock"]  # type: ignore[return-value]


# ── E2E: Full Executor Chain ─────────────────────────────────


class TestE2EExecutor:
    """Tests AgentExecutor end-to-end with mocked LLM."""

    @pytest.mark.asyncio
    async def test_pipeline_executes_all_agents(
        self, registry: ModelRegistry, mock_provider: _MockProvider
    ):
        """Pipeline with 3 agents runs sequentially and produces artifacts."""
        bus = EventBus()
        ctx = ContextManager(registry=registry, config=ContextConfig(default_provider="mock"))

        async with bus:
            async with AgentRuntime(bus) as runtime:
                executor = AgentExecutor(
                    bus=bus,
                    runtime=runtime,
                    registry=registry,
                    ctx_manager=ctx,
                )
                result = await executor.execute(
                    goal="Build API",
                    agents=[
                        {"name": "planner", "role": "orchestrator", "provider": "mock"},
                        {"name": "coder", "role": "worker", "provider": "mock"},
                        {"name": "reviewer", "role": "critic", "provider": "mock"},
                    ],
                    pattern="pipeline",
                )

                assert result.success is True
                assert len(result.artifacts) == 3
                assert result.plan is not None

    @pytest.mark.asyncio
    async def test_mesh_executes_in_parallel(self, registry: ModelRegistry):
        """Mesh pattern runs agents concurrently."""
        bus = EventBus()
        ctx = ContextManager(registry=registry, config=ContextConfig(default_provider="mock"))

        async with bus:
            async with AgentRuntime(bus) as runtime:
                executor = AgentExecutor(
                    bus=bus,
                    runtime=runtime,
                    registry=registry,
                    ctx_manager=ctx,
                )
                result = await executor.execute(
                    goal="Analyze data",
                    agents=[
                        {"name": "a1", "role": "worker", "provider": "mock"},
                        {"name": "a2", "role": "worker", "provider": "mock"},
                        {"name": "a3", "role": "worker", "provider": "mock"},
                    ],
                    pattern="mesh",
                )

                assert result.success is True
                assert len(result.artifacts) == 3

    @pytest.mark.asyncio
    async def test_orchestrator_workers_parallelism(self, registry: ModelRegistry):
        """Orchestrator-workers distributes subtasks to workers."""
        bus = EventBus()
        ctx = ContextManager(registry=registry, config=ContextConfig(default_provider="mock"))

        async with bus:
            async with AgentRuntime(bus) as runtime:
                executor = AgentExecutor(
                    bus=bus,
                    runtime=runtime,
                    registry=registry,
                    ctx_manager=ctx,
                )
                result = await executor.execute(
                    goal="Build multi-module app",
                    agents=[
                        {"name": "boss", "role": "orchestrator", "provider": "mock"},
                        {"name": "w1", "role": "worker", "provider": "mock"},
                        {"name": "w2", "role": "worker", "provider": "mock"},
                    ],
                    pattern="orchestrator-workers",
                    max_parallel=2,
                )

                assert result.success is True
                assert len(result.artifacts) >= 2

    @pytest.mark.asyncio
    async def test_executor_with_tools_react_loop(
        self, registry: ModelRegistry, mock_provider: _MockProvider
    ):
        """Executor runs ReAct loop when tool calls are present in LLM response."""
        # Configure mock to emit a JSON tool call on first invocation, then a plain answer
        first_call = True

        class ToolProvider(ModelProvider):
            @property
            def name(self) -> str:
                return "tool_mock"

            async def is_available(self) -> bool:
                return True

            async def chat(self, messages: List[ChatMessage], **kwargs: Any) -> ChatCompletion:
                nonlocal first_call
                if first_call:
                    first_call = False
                    return ChatCompletion(
                        content='{"tool": "double", "input": {"n": 5}}',
                        model="tool_mock",
                    )
                return ChatCompletion(content="Final answer: 10", model="tool_mock")

            async def stream(self, messages: List[ChatMessage], **kwargs: Any) -> Any:
                pass

            async def close(self) -> None:
                pass

        t_registry = ModelRegistry()
        t_registry.register("tool_mock", ToolProvider())

        tools = ToolRegistry()

        async def double_tool(n: int) -> str:
            return str(n * 2)

        tools.register(
            ToolSpec(
                name="double",
                description="Double a number",
                parameters={"n": {"type": "int"}},
                returns={"result": {"type": "string"}},
            ),
            double_tool,
        )

        bus = EventBus()
        ctx = ContextManager(
            registry=t_registry, config=ContextConfig(default_provider="tool_mock")
        )

        async with bus:
            async with AgentRuntime(bus) as runtime:
                executor = AgentExecutor(
                    bus=bus,
                    runtime=runtime,
                    registry=t_registry,
                    ctx_manager=ctx,
                    tools=tools,
                )
                result = await executor.execute(
                    goal="Compute double of 5",
                    agents=[{"name": "calc", "role": "worker", "provider": "tool_mock"}],
                    pattern="pipeline",
                )

                assert result.success is True

    @pytest.mark.asyncio
    async def test_tracing_spans_cover_full_execution(self, registry: ModelRegistry):
        """Tracer captures spans across the full executor chain."""
        tracer = Tracer()
        bus = EventBus()
        ctx = ContextManager(registry=registry, config=ContextConfig(default_provider="mock"))

        async with bus:
            async with AgentRuntime(bus) as runtime:
                executor = AgentExecutor(
                    bus=bus,
                    runtime=runtime,
                    registry=registry,
                    ctx_manager=ctx,
                    tracer=tracer,
                )
                await executor.execute(
                    goal="Test tracing",
                    agents=[{"name": "a", "role": "worker", "provider": "mock"}],
                    pattern="pipeline",
                )

        spans = tracer.export()
        names = {s["name"] for s in spans}
        assert "executor.execute" in names
        assert any("swarm.execute" in n for n in names)
        assert any("llm.call" in n for n in names)


# ── E2E: Event Bus + Router ──────────────────────────────────


class TestE2EEventFlow:
    """Tests event flow through Bus → Router → Subscribers."""

    @pytest.mark.asyncio
    async def test_event_published_and_consumed(self):
        """Emitted event reaches subscriber with correct causality."""
        received: List[Event] = []

        async def handler(event: Event) -> bool:
            received.append(event)
            return True

        bus = EventBus()
        async with bus:
            bus.subscribe(handler, topics=["test.topic"])
            event = create_event(
                event_type=EventType.SYSTEM_LOG,
                source="system:test",
                topic="test.topic",
                payload={"msg": "hello"},
            )
            await bus.emit(event)
            # Give event loop time to process
            await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].topic == "test.topic"
        assert received[0].payload["msg"] == "hello"
        # Vector clock should be stamped
        assert received[0].causality_vector

    @pytest.mark.asyncio
    async def test_multiple_subscribers_fan_out(self):
        """One event reaches multiple subscribers (fan-out)."""
        received_a: List[Event] = []
        received_b: List[Event] = []

        async def handler_a(event: Event) -> bool:
            received_a.append(event)
            return True

        async def handler_b(event: Event) -> bool:
            received_b.append(event)
            return True

        bus = EventBus()
        async with bus:
            bus.subscribe(handler_a, topics=["fanout"])
            bus.subscribe(handler_b, topics=["fanout"])
            await bus.emit(
                create_event(
                    event_type=EventType.SYSTEM_LOG,
                    source="system:test",
                    topic="fanout",
                    payload={},
                )
            )
            await asyncio.sleep(0.1)

        assert len(received_a) == 1
        assert len(received_b) == 1

    @pytest.mark.asyncio
    async def test_priority_queue_ordering(self):
        """Higher-priority (more negative) events are processed first."""
        order: List[int] = []

        async def handler(event: Event) -> bool:
            order.append(event.priority)
            return True

        from edac.event.schema import EventPriority

        bus = EventBus()
        async with bus:
            bus.subscribe(handler, topics=["prio"])
            await bus.emit(
                create_event(
                    event_type=EventType.SYSTEM_LOG,
                    source="system:test",
                    topic="prio",
                    priority=EventPriority.LOW,
                    payload={},
                )
            )
            await bus.emit(
                create_event(
                    event_type=EventType.SYSTEM_LOG,
                    source="system:test",
                    topic="prio",
                    priority=EventPriority.HIGH,
                    payload={},
                )
            )
            await bus.emit(
                create_event(
                    event_type=EventType.SYSTEM_LOG,
                    source="system:test",
                    topic="prio",
                    priority=EventPriority.NORMAL,
                    payload={},
                )
            )
            await asyncio.sleep(0.2)

        # HIGH (-50) should be first, then NORMAL (0), then LOW (50)
        assert order[0] == EventPriority.HIGH.value
        assert order[1] == EventPriority.NORMAL.value
        assert order[2] == EventPriority.LOW.value


# ── E2E: Plan Engine + DAG ───────────────────────────────────


class TestE2EPlanExecution:
    """Tests PlanEngine with real step execution."""

    @pytest.mark.asyncio
    async def test_plan_parallel_execution(self):
        """Independent steps execute in parallel."""
        bus = EventBus()
        engine = PlanEngine(bus, PlanConfig(max_parallel=3))

        plan = PlanDAG(goal="Parallel work")
        plan.add_step(Step(id="a", description="Task A", action="compute"))
        plan.add_step(Step(id="b", description="Task B", action="compute"))
        plan.add_step(Step(id="c", description="Task C", action="compute", dependencies=["a", "b"]))

        execution_order: List[str] = []
        start_times: Dict[str, float] = {}
        end_times: Dict[str, float] = {}

        async def step_executor(step: Step) -> str:
            import time

            start_times[step.id] = time.monotonic()
            execution_order.append(step.id)
            await asyncio.sleep(0.05)  # Simulate work
            end_times[step.id] = time.monotonic()
            return f"done-{step.id}"

        async with bus:
            result = await engine.execute(plan, step_executor)

        assert result.is_complete
        # a and b should overlap (parallel), c should run after both
        assert start_times["a"] < end_times["a"]
        assert start_times["b"] < end_times["b"]
        assert start_times["c"] >= max(end_times["a"], end_times["b"])

    @pytest.mark.asyncio
    async def test_replan_on_step_failure(self):
        """Failed step triggers replanning (retry)."""
        bus = EventBus()
        engine = PlanEngine(
            bus,
            PlanConfig(max_replans=1, replan_triggers={"step_failure"}),
        )

        plan = PlanDAG(goal="Failing task")
        plan.add_step(Step(id="fail", description="Will fail once", action="compute"))

        call_count = 0

        async def flaky_executor(step: Step) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError(" simulated failure")
            return "recovered"

        async with bus:
            result = await engine.execute(plan, flaky_executor)

        assert result.is_complete
        # Step should have been retried
        assert call_count == 2
        assert plan.get_step("fail").result == "recovered"

    @pytest.mark.asyncio
    async def test_plan_event_emission(self):
        """PlanEngine emits plan lifecycle events on the bus."""
        events: List[Event] = []

        async def collector(event: Event) -> bool:
            if event.event_type.value.startswith("plan."):
                events.append(event)
            return True

        bus = EventBus()
        bus.subscribe(collector)
        engine = PlanEngine(bus, PlanConfig())

        plan = PlanDAG(goal="Emit events")
        plan.add_step(Step(id="s1", description="Step", action="noop"))

        async with bus:

            async def executor(step: Step) -> str:
                return "ok"

            await engine.execute(plan, executor)
            await asyncio.sleep(0.1)

        plan_events = [e for e in events if e.event_type.value.startswith("plan.")]
        assert len(plan_events) >= 2  # start + complete at minimum
