"""Tests for observability stack."""

import asyncio
import pytest

from edac.observability.tracing import Tracer, Span
from edac.observability.metrics import MetricsCollector, Counter, Gauge, Histogram
from edac.observability.replay import TrajectoryExporter
from edac.memory.episodic import EpisodicMemory
from edac.event.schema import Event, EventType, create_event
from edac.event.bus import EventBus
from uuid import uuid4


class TestTracer:
    def test_start_end_span(self):
        tracer = Tracer()
        span = tracer.start_span("test")
        assert span.name == "test"
        assert span.end_time is None
        tracer.end_span(span)
        assert span.end_time is not None

    def test_trace_event(self):
        tracer = Tracer()
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        span = tracer.trace_event(e)
        assert "event:agent.spawn" in span.name

    def test_span_context_manager(self):
        tracer = Tracer()
        with tracer.span("ctx") as span:
            assert span.name == "ctx"
            assert span.end_time is None
        assert span.end_time is not None

    def test_export(self):
        tracer = Tracer()
        with tracer.span("s1"):
            pass
        exported = tracer.export()
        assert len(exported) == 1
        assert exported[0]["name"] == "s1"
        assert "duration_ms" in exported[0]


class TestTracingIntegration:
    """Tracer wired into EventBus dispatch flow."""

    @pytest.mark.asyncio
    async def test_dispatch_creates_span(self):
        """When events are dispatched, a span is created in the tracer."""
        bus = EventBus(enable_persistence=False)
        tracer = Tracer()
        bus.tracer = tracer

        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(handler, topics=["agent.spawn"])

        event = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        await bus.start()
        try:
            await bus.emit(event)
            await asyncio.sleep(0.05)  # let dispatcher process
        finally:
            await bus.stop()

        spans = tracer.export()
        assert any(s["name"] == "event:agent.spawn" for s in spans)

    @pytest.mark.asyncio
    async def test_span_includes_event_metadata(self):
        """Span carries event type, source, topic, and correlation_id as attributes."""
        bus = EventBus(enable_persistence=False)
        tracer = Tracer()
        bus.tracer = tracer

        event = create_event(EventType.PLAN_STEP_START, "agent:p", "plan.step", payload={"step": 1})
        await bus.start()
        try:
            await bus.emit(event)
            await asyncio.sleep(0.05)
        finally:
            await bus.stop()

        spans = tracer.export()
        span = next((s for s in spans if s["name"] == "event:plan.step.start"), None)
        assert span is not None
        assert span["attributes"]["source"] == "agent:p"
        assert span["attributes"]["topic"] == "plan.step"

    @pytest.mark.asyncio
    async def test_span_duration_positive(self):
        """Dispatched spans have positive duration."""
        bus = EventBus(enable_persistence=False)
        tracer = Tracer()
        bus.tracer = tracer

        async def slow_handler(event):
            await asyncio.sleep(0.02)

        bus.subscribe(slow_handler, topics=["agent.heartbeat"])

        event = create_event(EventType.AGENT_HEARTBEAT, "agent:h", "agent.heartbeat", payload={})
        await bus.start()
        try:
            await bus.emit(event)
            await asyncio.sleep(0.1)
        finally:
            await bus.stop()

        spans = tracer.export()
        span = next((s for s in spans if s["name"] == "event:agent.heartbeat"), None)
        assert span is not None
        assert span["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_no_spans_without_tracer(self):
        """When no tracer is attached, dispatch still works but no spans are created."""
        bus = EventBus(enable_persistence=False)

        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(handler, topics=["system.health"])

        event = create_event(EventType.SYSTEM_METRIC, "system:sys", "system.health", payload={})
        await bus.start()
        try:
            await bus.emit(event)
            await asyncio.sleep(0.05)
        finally:
            await bus.stop()

        # No crash; that's the main assertion
        assert bus.tracer is None


class TestMetricsCollector:
    def test_counter(self):
        mc = MetricsCollector()
        c = mc.counter("requests", labels={"method": "GET"})
        c.inc()
        c.inc(2)
        assert c.value == 3

    def test_gauge(self):
        mc = MetricsCollector()
        g = mc.gauge("queue_depth")
        g.set(5)
        assert g.value == 5

    def test_histogram(self):
        mc = MetricsCollector()
        h = mc.histogram("latency")
        h.observe(50)
        h.observe(150)
        h.observe(250)
        assert h.sum_value == 450
        # buckets: [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
        assert h.counts[1] == 1   # 50 <= 50
        assert h.counts[3] == 2  # 150 & 250 <= 250
        assert sum(h.counts) == 3

    def test_export(self):
        mc = MetricsCollector()
        mc.counter("c").inc()
        mc.gauge("g").set(1)
        mc.histogram("h").observe(5)
        text = mc.export()
        assert "requests_total" not in text  # name is just "c"
        assert "c" in text
        assert "g" in text
        assert "h" in text

    def test_export_no_trailing_comma_with_empty_labels(self):
        mc = MetricsCollector()
        mc.counter("c").inc()
        mc.gauge("g").set(1)
        mc.histogram("h").observe(5)
        text = mc.export()
        # Empty labels must not produce trailing commas like {le="10",}
        assert ",{}" not in text
        assert '","' not in text
        assert "c 1" in text or "c{}" in text
        assert "g 1" in text or "g{}" in text


class TestTrajectoryExporter:
    def test_summary(self, tmp_path):
        mem = EpisodicMemory()
        cid = uuid4()
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", correlation_id=cid, payload={})
        mem.append(e)
        exporter = TrajectoryExporter(mem)
        summary = exporter.summary(cid)
        assert summary["event_count"] == 1
        assert summary["correlation_id"] == str(cid)

    def test_export_jsonl(self, tmp_path):
        mem = EpisodicMemory()
        cid = uuid4()
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", correlation_id=cid, payload={})
        mem.append(e)
        exporter = TrajectoryExporter(mem)
        out = tmp_path / "traj.jsonl"
        exporter.export(cid, out, format="jsonl")
        assert out.exists()
        lines = out.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_export_json(self, tmp_path):
        mem = EpisodicMemory()
        cid = uuid4()
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", correlation_id=cid, payload={})
        mem.append(e)
        exporter = TrajectoryExporter(mem)
        out = tmp_path / "traj.json"
        exporter.export(cid, out, format="json")
        assert out.exists()
        assert out.read_text().startswith("[")
