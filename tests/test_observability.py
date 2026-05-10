"""Tests for observability stack."""

import pytest

from edac.observability.tracing import Tracer, Span
from edac.observability.metrics import MetricsCollector, Counter, Gauge, Histogram
from edac.observability.replay import TrajectoryExporter
from edac.memory.episodic import EpisodicMemory
from edac.event.schema import Event, EventType, create_event
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
