"""Tests for the event schema, bus, and router."""

import asyncio
import pytest
from datetime import datetime, timezone
from uuid import uuid4

from edac.event.schema import (
    Event,
    EventType,
    EventPriority,
    EventFilter,
    create_event,
    create_system_event,
)
from edac.event.bus import EventBus, EventBusConfig, BusStats
from edac.event.router import EventRouter, Subscription, RouterConfig, TopicMatcher
from edac.observability.tracing import Tracer


class TestEventSchema:
    def test_event_creation(self):
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={"name": "test"})
        assert e.event_type == EventType.AGENT_SPAWN
        assert e.source == "agent:a"
        assert e.topic == "agent.spawn"
        assert "name" in e.payload

    def test_event_correlation(self):
        cid = uuid4()
        e1 = create_event(
            EventType.AGENT_SPAWN, "agent:a", "agent.spawn", correlation_id=cid, payload={}
        )
        e2 = create_event(
            EventType.AGENT_HEARTBEAT,
            "agent:a",
            "agent.health",
            correlation_id=cid,
            parent_event_id=e1.event_id,
            payload={},
        )
        assert e2.parent_event_id == e1.event_id
        assert e2.correlation_id == cid

    def test_event_filter_match(self):
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={"name": "test"})
        f = EventFilter(event_types=[EventType.AGENT_SPAWN])
        assert f.matches(e)

        f2 = EventFilter(topics=["agent.health"])
        assert not f2.matches(e)

    def test_event_causality(self):
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        assert e.causality_vector == {}
        assert e.event_id is not None
        assert e.timestamp is not None


class TestEventBus:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        bus = EventBus()
        async with bus:
            assert bus._running
        assert not bus._running

    @pytest.mark.asyncio
    async def test_emit_and_subscribe(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)
            return None

        async with bus:
            sub = bus.subscribe(handler, topics=["agent.test"])
            e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.test", payload={})
            await bus.emit(e)
            await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].event_type == EventType.AGENT_SPAWN

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)
            return None

        async with bus:
            sub = bus.subscribe(handler, topics=["agent.test"])
            bus.unsubscribe(sub)
            e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.test", payload={})
            await bus.emit(e)
            await asyncio.sleep(0.1)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_handler_chain(self):
        bus = EventBus()

        async def handler(event):
            return event.derive(
                EventType.AGENT_HEARTBEAT,
                payload={"chained": True},
                topic="agent.heartbeat",
            )

        async with bus:
            bus.subscribe(handler, topics=["agent.test"])
            e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.test", payload={})
            await bus.emit(e)
            await asyncio.sleep(0.1)
            log = bus.get_event_log()
            types = [ev.event_type for ev in log]
            assert EventType.AGENT_HEARTBEAT in types

    @pytest.mark.asyncio
    async def test_backpressure_drop(self):
        bus = EventBus(max_queue_depth=1)
        async with bus:
            # Fill queue
            e1 = create_event(
                EventType.SYSTEM_LOG,
                "system:s",
                "system.log",
                payload={},
                priority=EventPriority.NORMAL,
            )
            e2 = create_event(
                EventType.SYSTEM_LOG,
                "system:s",
                "system.log",
                payload={},
                priority=EventPriority.NORMAL,
            )
            await bus.emit(e1)
            # Second event should be dropped under backpressure
            result = await bus.emit(e2)
            assert result is False

    @pytest.mark.asyncio
    async def test_concurrent_emit_respects_max_depth(self):
        bus = EventBus(max_queue_depth=5)
        async with bus:
            # Slow handler so queue backs up
            handler_done = asyncio.Event()

            async def slow_handler(event):
                await handler_done.wait()
                return None

            bus.subscribe(slow_handler, topics=["test"])

            events = [
                create_event(
                    EventType.SYSTEM_LOG,
                    "system:s",
                    "test",
                    payload={},
                    priority=EventPriority.NORMAL,
                )
                for _ in range(20)
            ]
            results = await asyncio.gather(*[bus.emit(e) for e in events])
            accepted = sum(results)
            # With serialized backpressure, no more than max_queue_depth + 1 should be accepted
            assert accepted <= 6, f"Accepted {accepted} events but max_queue_depth is 5"
            handler_done.set()

    @pytest.mark.asyncio
    async def test_stream(self):
        bus = EventBus()
        async with bus:
            # Just verify stream can be created and closed without hanging
            async with bus.stream(topics=["test"]) as events:
                pass

    # ── Tracing Integration Tests ──

    @pytest.mark.asyncio
    async def test_emit_without_tracer_no_overhead(self):
        """When no tracer is provided, emit behaves identically."""
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)
            return None

        async with bus:
            bus.subscribe(handler, topics=["agent.test"])
            e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.test", payload={})
            result = await bus.emit(e)
            await asyncio.sleep(0.1)

        assert result is True
        assert len(received) == 1
        # No tracer attached; no spans collected
        assert bus.get_trace_spans() == []

    @pytest.mark.asyncio
    async def test_emit_with_tracer_creates_spans(self):
        """When tracer is provided, each emit creates a finished span."""
        tracer = Tracer()
        bus = EventBus(tracer=tracer)

        async def handler(event):
            return None

        async with bus:
            bus.subscribe(handler, topics=["agent.test"])
            e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.test", payload={})
            await bus.emit(e)
            await asyncio.sleep(0.1)

        spans = bus.get_trace_spans()
        assert len(spans) == 1
        assert spans[0]["name"] == "event:agent.spawn"
        assert spans[0]["trace_id"] is not None
        assert spans[0]["span_id"] is not None
        assert spans[0]["attributes"]["event_type"] == "agent.spawn"
        assert spans[0]["attributes"]["source"] == "agent:a"
        assert spans[0]["attributes"]["topic"] == "agent.test"
        assert "duration_ms" in spans[0]

    @pytest.mark.asyncio
    async def test_tracer_not_shared_across_emit(self):
        """Each bus has independent tracer state."""
        tracer = Tracer()
        bus1 = EventBus(tracer=tracer)
        bus2 = EventBus()

        async with bus1:
            await bus1.emit(
                create_event(EventType.AGENT_SPAWN, "agent:a", "agent.test", payload={})
            )
            await asyncio.sleep(0.1)

        async with bus2:
            await bus2.emit(
                create_event(EventType.AGENT_SPAWN, "agent:a", "agent.test", payload={})
            )
            await asyncio.sleep(0.1)

        assert len(bus1.get_trace_spans()) == 1
        assert len(bus2.get_trace_spans()) == 0

    @pytest.mark.asyncio
    async def test_tracer_spans_include_correlation_id(self):
        """Spans carry the event's correlation_id for distributed tracing."""
        tracer = Tracer()
        bus = EventBus(tracer=tracer)
        cid = uuid4()

        async with bus:
            e = create_event(
                EventType.AGENT_SPAWN, "agent:a", "agent.test", correlation_id=cid, payload={}
            )
            await bus.emit(e)
            await asyncio.sleep(0.1)

        spans = bus.get_trace_spans()
        assert len(spans) == 1
        assert spans[0]["attributes"]["correlation_id"] == str(cid)


class TestEventRouter:
    @pytest.mark.asyncio
    async def test_basic_routing(self):
        router = EventRouter()
        received = []

        async def handler(event):
            received.append(event)
            return None

        router.subscribe("agent.spawn", handler)
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        await router.route(e)

        assert len(received) == 1
        assert received[0].event_type == EventType.AGENT_SPAWN

    @pytest.mark.asyncio
    async def test_wildcard_routing(self):
        router = EventRouter()
        received = []

        async def handler(event):
            received.append(event)
            return None

        router.subscribe("agent.*", handler)
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        await router.route(e)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_filter(self):
        router = EventRouter()
        received = []

        async def handler(event):
            received.append(event)
            return None

        def only_heartbeats(event):
            return event.event_type == EventType.AGENT_HEARTBEAT

        router.subscribe("agent.*", handler, filter=only_heartbeats)
        e1 = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        e2 = create_event(EventType.AGENT_HEARTBEAT, "agent:a", "agent.heartbeat", payload={})
        await router.route(e1)
        await router.route(e2)

        assert len(received) == 1
        assert received[0].event_type == EventType.AGENT_HEARTBEAT

    def test_unsubscribe(self):
        router = EventRouter()
        sub = router.subscribe("agent.spawn", lambda e: None)
        assert router.unsubscribe(sub.id)
        assert not router.unsubscribe("nonexistent")

    def test_topic_matcher(self):
        assert TopicMatcher.matches("agent.spawn", "agent.*")
        assert TopicMatcher.matches("agent.plan.step.start", "agent.**")
        assert not TopicMatcher.matches("plan.create", "agent.*")

    def test_router_stats(self):
        router = EventRouter()
        stats = router.get_stats()
        assert "events_routed" in stats

    def test_routing_table(self):
        router = EventRouter()

        async def h1(e):
            pass

        async def h2(e):
            pass

        s1 = router.subscribe("a.b", h1)
        s2 = router.subscribe("a.*", h2)
        subs = router.get_subscribers("a.b")
        assert len(subs) == 2

        router.unsubscribe(s1.id)
        subs = router.get_subscribers("a.b")
        assert len(subs) == 1
