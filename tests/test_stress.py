"""Stress tests for stability under load.

Tests concurrent execution, large data structures, and edge cases
that could expose race conditions or resource leaks.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from edac.agent.runtime import AgentRuntime
from edac.context.budget import BudgetTracker
from edac.context.manager import ContextConfig, ContextManager
from edac.event.bus import EventBus
from edac.event.schema import EventType, create_event
from edac.event.vector_clock import VectorClock
from edac.memory.episodic import EpisodicMemory
from edac.memory.long_term import LongTermMemory
from edac.model import ModelRegistry
from edac.observability.tracing import Tracer
from edac.plan.dag import PlanDAG, Step, StepStatus
from edac.plan.engine import PlanEngine, PlanConfig
from edac.swarm import Swarm
from edac.tool.registry import ToolRegistry, ToolSpec
from edac.examples._utils import MockProvider


class TestConcurrencyStress:
    """Stress-test concurrent operations."""

    @pytest.mark.asyncio
    async def test_concurrent_emit_no_race(self):
        """100 concurrent emits don't corrupt bus state."""
        received = 0

        async def handler(event) -> bool:
            nonlocal received
            received += 1
            return True

        bus = EventBus()
        bus.subscribe(handler)

        async with bus:
            async def emit_one(i: int):
                await bus.emit(create_event(
                    event_type=EventType.SYSTEM_LOG,
                    source="system:stress",
                    topic="stress.concurrent",
                    payload={"i": i},
                ))

            await asyncio.gather(*[emit_one(i) for i in range(100)])
            await asyncio.sleep(0.2)  # drain

        assert received == 100
        assert bus.get_stats().events_emitted == 100

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_unsubscribe(self):
        """Rapid subscribe/unsubscribe doesn't crash."""
        bus = EventBus()
        subs = []

        async def noop(event) -> bool:
            return True

        async with bus:
            for i in range(50):
                sub = bus.subscribe(noop, topics=[f"topic_{i}"])
                subs.append(sub)

            assert bus.get_stats().active_subscriptions == 50

            for sub in subs:
                bus.unsubscribe(sub)

            await asyncio.sleep(0.1)
            assert bus.get_stats().active_subscriptions == 0

    @pytest.mark.asyncio
    async def test_mesh_swarm_concurrent_agents(self):
        """Mesh with 10 agents runs without deadlock."""
        bus = EventBus()
        registry = ModelRegistry()
        registry.register("mock", MockProvider(default_response="ok"))

        async with bus:
            async with AgentRuntime(bus) as runtime:
                swarm = Swarm(
                    bus=bus,
                    runtime=runtime,
                    pattern="mesh",
                    agents=[{"name": f"a{i}", "role": "worker"} for i in range(10)],
                    max_parallel=5,
                )
                result = await swarm.execute(goal="Stress test")
                assert result.success is True
                assert len(result.artifacts) == 10


class TestLargeDataStructures:
    """Stress-test with large inputs."""

    def test_very_large_plan_dag(self):
        """DAG with 500 steps resolves without recursion error."""
        dag = PlanDAG(goal="Massive DAG")
        for i in range(500):
            deps = [f"step_{i-1}"] if i > 0 else []
            dag.add_step(Step(id=f"step_{i}", description="work", action="noop", dependencies=deps))

        order = dag.topological_order()
        assert len(order) == 500
        groups = dag.parallel_groups()
        # Each step depends on previous, so each group has 1 step
        assert len(groups) == 500

    def test_wide_parallel_dag(self):
        """DAG with 100 independent steps → 1 merge step."""
        dag = PlanDAG(goal="Wide DAG")
        for i in range(100):
            dag.add_step(Step(id=f"independent_{i}", description="work", action="noop"))
        dag.add_step(Step(id="merge", description="collect", action="noop", dependencies=[f"independent_{i}" for i in range(100)]))

        groups = dag.parallel_groups()
        # First group: 100 independent steps. Second group: merge step.
        assert len(groups) == 2
        assert len(groups[0]) == 100

    def test_long_term_memory_overflow(self):
        """Long-term memory handles 2000 entries."""
        mem = LongTermMemory()
        for i in range(2000):
            mem.store(content=f"Entry {i}", metadata={"index": i})

        results = mem.search_text("Entry", top_k=50)
        assert len(results) == 50

    def test_budget_tracker_high_volume(self):
        """Budget tracker handles 10000 rapid consume calls."""
        bt = BudgetTracker(agent_limit=1_000_000)
        for i in range(10000):
            bt.consume(f"agent_{i % 10}", 1)

        assert bt.agent_usage("agent_0") == 1000
        assert not bt.is_over_budget("agent_0")


class TestVectorClockStress:
    """Stress-test vector clock operations."""

    def test_merge_many_clocks(self):
        """Merging 100 clocks is correct and fast."""
        import time

        clocks = [VectorClock({f"node_{i}": i}) for i in range(100)]
        base = VectorClock()
        base = VectorClock()

        start = time.monotonic()
        for c in clocks:
            base.merge(c)
        elapsed = (time.monotonic() - start) * 1000

        assert base._clock["node_99"] == 99
        assert elapsed < 50.0

    def test_concurrent_event_ordering(self):
        """100 events from different nodes maintain partial order."""
        events = []
        for i in range(100):
            vc = VectorClock()
            for j in range(i + 1):
                vc.increment(f"node_{j % 10}")
            events.append(vc)

        # All later events should happen_after or be concurrent with earlier ones
        for i in range(1, 100):
            assert events[i].happens_after(events[i - 1]) or events[i].concurrent_with(events[i - 1])


class TestTracerStress:
    """Stress-test tracing under load."""

    def test_1000_spans_no_leak(self):
        """Tracer handles 1000 nested spans without memory issues."""
        tracer = Tracer()
        for i in range(1000):
            span = tracer.start_span(f"span_{i}")
            tracer.end_span(span)

        exported = tracer.export()
        assert len(exported) == 1000

    def test_deeply_nested_spans(self):
        """100-level deep nesting works."""
        tracer = Tracer()
        spans = []
        parent = tracer.start_span("root")
        spans.append(parent)
        for i in range(100):
            child = tracer.start_span(f"level_{i}", parent=spans[-1])
            spans.append(child)

        for span in reversed(spans):
            tracer.end_span(span)

        exported = tracer.export()
        assert len(exported) == 101
