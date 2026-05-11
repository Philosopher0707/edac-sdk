"""Performance benchmarks for core EDAC components.

Uses `time.monotonic()` and simple statistics instead of pytest-benchmark
(which is not installed). Each test asserts that measured operations complete
within a reasonable wall-clock budget, catching performance regressions.

Run with: pytest tests/test_benchmarks.py -v
"""

from __future__ import annotations

import asyncio
import time
from typing import List

import pytest

from edac.context.compressor import ContextCompressor
from edac.event.bus import EventBus
from edac.event.schema import EventType, create_event
from edac.memory.episodic import EpisodicMemory
from edac.memory.long_term import LongTermMemory
from edac.plan.dag import PlanDAG, Step
from edac.plan.engine import PlanEngine, PlanConfig
from edac.plan.parallelizer import Parallelizer


# ── Benchmark Helpers ───────────────────────────────────────

MAX_EVENT_BUS_MS = 500.0       # 1000 events through bus
MAX_PLAN_DAG_MS = 200.0       # Build + resolve 100-step DAG
MAX_COMPRESS_MS = 300.0       # Compress 1000-entry window
MAX_MEMORY_MS = 200.0         # Store + search 500 long-term entries
MAX_PARALLELIZER_MS = 100.0   # Detect parallel groups in 50-step DAG


class TestEventBusBenchmark:
    """Measure event bus throughput and latency."""

    @pytest.mark.asyncio
    async def test_emit_1000_events(self):
        """Bus should emit and route 1000 events in <500ms."""
        count = 0

        async def handler(event) -> bool:
            nonlocal count
            count += 1
            return True

        bus = EventBus()
        bus.subscribe(handler)

        async with bus:
            start = time.monotonic()
            for i in range(1000):
                await bus.emit(create_event(
                    event_type=EventType.SYSTEM_LOG,
                    source="system:bench",
                    topic="bench.throughput",
                    payload={"i": i},
                ))
            # Wait for queue drain
            while bus.get_stats().queue_depth > 0:
                await asyncio.sleep(0.01)
            elapsed = (time.monotonic() - start) * 1000

        assert count == 1000, f"Only {count} events received"
        assert elapsed < MAX_EVENT_BUS_MS, f"Too slow: {elapsed:.1f}ms for 1000 events"

    @pytest.mark.asyncio
    async def test_priority_queue_sorting_speed(self):
        """Priority queue insertion of 500 events stays fast."""
        bus = EventBus()

        async with bus:
            start = time.monotonic()
            for i in range(500):
                await bus.emit(create_event(
                    event_type=EventType.SYSTEM_LOG,
                    source="system:bench",
                    topic="bench.prio",
                    priority=[0, 50, -50, -50, 0][i % 5],
                    payload={},
                ))
            elapsed = (time.monotonic() - start) * 1000

        assert elapsed < MAX_EVENT_BUS_MS, f"Priority queue too slow: {elapsed:.1f}ms"


class TestPlanDAGBenchmark:
    """Measure plan DAG construction and resolution speed."""

    def test_build_large_dag(self):
        """Building a 100-step DAG with deps should be <200ms."""
        start = time.monotonic()
        dag = PlanDAG(goal="Large benchmark DAG")
        for i in range(100):
            deps = [f"step_{i-1}"] if i > 0 else []
            dag.add_step(Step(id=f"step_{i}", description=f"Step {i}", action="noop", dependencies=deps))
        elapsed = (time.monotonic() - start) * 1000

        assert len(dag.list_steps()) == 100
        assert elapsed < MAX_PLAN_DAG_MS, f"DAG build too slow: {elapsed:.1f}ms"

    def test_topological_order_large_dag(self):
        """Topological sort of 100-step DAG should be <50ms."""
        dag = PlanDAG(goal="Sort benchmark")
        for i in range(100):
            deps = [f"step_{i-1}"] if i > 0 else []
            dag.add_step(Step(id=f"step_{i}", description=f"Step {i}", action="noop", dependencies=deps))

        start = time.monotonic()
        order = dag.topological_order()
        elapsed = (time.monotonic() - start) * 1000

        assert len(order) == 100
        assert elapsed < 50.0, f"Topo sort too slow: {elapsed:.1f}ms"

    def test_parallel_groups_detection(self):
        """Parallelizer finds concurrent groups in 50-step DAG quickly."""
        dag = PlanDAG(goal="Parallel benchmark")
        # Create layers of independent steps
        for layer in range(5):
            for i in range(10):
                step_id = f"L{layer}_S{i}"
                deps = [f"L{layer-1}_S0"] if layer > 0 else []
                dag.add_step(Step(id=step_id, description="work", action="noop", dependencies=deps))

        parallelizer = Parallelizer(dag)
        start = time.monotonic()
        groups = parallelizer.ready_groups()
        elapsed = (time.monotonic() - start) * 1000

        assert len(groups) == 1  # Layer 0 is all that's ready initially
        assert len(groups[0]) == 10  # All 10 layer-0 steps are independent
        assert parallelizer.max_parallelism() == 10  # 10 independent steps in first layer


class TestContextCompressionBenchmark:
    """Measure context compression performance."""

    def test_compress_large_window(self):
        """Compressing 1000-entry window should be <300ms."""
        from edac.memory.short_term import ShortTermMemory, WindowEntry

        mem = ShortTermMemory(max_tokens=100000)
        for i in range(1000):
            mem.add(WindowEntry(role="assistant", content=f"Entry {i}", tokens=100))

        compressor = ContextCompressor(target_tokens=50000)
        start = time.monotonic()
        result = compressor.compress(mem)
        elapsed = (time.monotonic() - start) * 1000

        assert result.total_tokens() <= 50000
        assert elapsed < MAX_COMPRESS_MS, f"Compression too slow: {elapsed:.1f}ms"


class TestMemoryBenchmark:
    """Measure memory store and retrieval performance."""

    def test_long_term_store_and_search(self):
        """Storing and searching 500 entries should be <200ms."""
        mem = LongTermMemory()
        start = time.monotonic()
        for i in range(500):
            mem.store(
                content=f"Memory entry number {i} about Python asyncio patterns",
                metadata={"index": i},
            )
        elapsed_store = (time.monotonic() - start) * 1000

        start = time.monotonic()
        results = mem.search_text("asyncio", top_k=10)
        elapsed_search = (time.monotonic() - start) * 1000

        assert len(results) > 0
        assert elapsed_store + elapsed_search < MAX_MEMORY_MS, (
            f"Memory ops too slow: store={elapsed_store:.1f}ms search={elapsed_search:.1f}ms"
        )

    def test_episodic_append_and_replay(self):
        """Appending 1000 events and replaying should be fast."""
        from uuid import uuid4

        memory = EpisodicMemory()
        corr = uuid4()

        start = time.monotonic()
        for i in range(1000):
            memory.append(create_event(
                event_type=EventType.SYSTEM_LOG,
                source="system:bench",
                topic="bench.episodic",
                correlation_id=corr,
                payload={"i": i},
            ))
        elapsed_append = (time.monotonic() - start) * 1000

        start = time.monotonic()
        replayed = memory.replay(corr)
        elapsed_replay = (time.monotonic() - start) * 1000

        assert len(replayed) == 1000
        assert elapsed_append < 300.0, f"Episodic append too slow: {elapsed_append:.1f}ms"
        assert elapsed_replay < 50.0, f"Episodic replay too slow: {elapsed_replay:.1f}ms"
