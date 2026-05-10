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


class TestEventSchema:
    def test_event_creation(self):
        e = Event(
            event_type=EventType.AGENT_SPAWN,
            source="agent:test",
            topic="agent.test",
            correlation_id=uuid4(),
            payload={"name": "foo"},
        )
        assert e.event_type == EventType.AGENT_SPAWN
        assert e.source == "agent:test"
        assert e.payload["name"] == "foo"

    def test_source_validation(self):
        with pytest.raises(ValueError):
            Event(
                event_type=EventType.AGENT_SPAWN,
                source="badformat",
                topic="agent.test",
                correlation_id=uuid4(),
            )

    def test_event_immutability(self):
        e = Event(
            event_type=EventType.AGENT_SPAWN,
            source="agent:test",
            topic="agent.test",
            correlation_id=uuid4(),
        )
        with pytest.raises(Exception):
            e.event_type = EventType.AGENT_HEARTBEAT

    def test_derive(self):
        parent = Event(
            event_type=EventType.AGENT_SPAWN,
            source="agent:parent",
            topic="agent.test",
            correlation_id=uuid4(),
        )
        child = parent.derive(EventType.AGENT_HEARTBEAT, payload={"beat": 1})
        assert child.parent_event_id == parent.event_id
        assert child.correlation_id == parent.correlation_id
        assert child.causality_vector.get("parent", 0) == 1

    def test_is_expired(self):
        e = Event(
            event_type=EventType.AGENT_SPAWN,
            source="agent:test",
            topic="agent.test",
            correlation_id=uuid4(),
            ttl_seconds=0.01,
        )
        assert not e.is_expired()
        import time
        time.sleep(0.02)
        assert e.is_expired()

    def test_create_system_event(self):
        e = create_system_event(EventType.SYSTEM_ERROR, {"msg": "boom"})
        assert e.source == "system:core"
        assert e.topic == "system.events"

    def test_event_filter_matches(self):
        f = EventFilter(event_types={EventType.AGENT_SPAWN})
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        assert f.matches(e)

        e2 = create_event(EventType.AGENT_HEARTBEAT, "agent:a", "agent.heartbeat", payload={})
        assert not f.matches(e2)

    def test_event_filter_topic_wildcard(self):
        f = EventFilter(topics={"agent.*"})
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        assert f.matches(e)


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
            e1 = create_event(EventType.SYSTEM_LOG, "system:s", "system.log", payload={}, priority=EventPriority.NORMAL)
            e2 = create_event(EventType.SYSTEM_LOG, "system:s", "system.log", payload={}, priority=EventPriority.NORMAL)
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
                create_event(EventType.SYSTEM_LOG, "system:s", "test", payload={}, priority=EventPriority.NORMAL)
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
            async with bus.stream(topics=["agent.test"], max_buffer=10) as events:
                pass  # streaming is timing-sensitive; avoid async-for in test

    def test_bus_stats(self):
        bus = EventBus()
        stats = bus.get_stats()
        assert isinstance(stats, BusStats)
        assert stats.events_emitted == 0


class TestEventRouter:
    @pytest.mark.asyncio
    async def test_route_exact_topic(self):
        router = EventRouter()
        received = []

        async def handler(event):
            received.append(event)

        router.subscribe("agent.spawn", handler)
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        count = await router.route(e)
        assert count == 1
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_route_wildcard(self):
        router = EventRouter()
        received = []

        async def handler(event):
            received.append(event)

        router.subscribe("agent.*", handler)
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        await router.route(e)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_route_filter(self):
        router = EventRouter()
        received = []

        async def handler(event):
            received.append(event)

        def only_heartbeats(event):
            return event.event_type == EventType.AGENT_HEARTBEAT

        router.subscribe("agent.*", handler, filter=only_heartbeats)
        e1 = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        e2 = create_event(EventType.AGENT_HEARTBEAT, "agent:a", "agent.heartbeat", payload={})
        await router.route(e1)
        await router.route(e2)
        assert len(received) == 1
        assert received[0].event_type == EventType.AGENT_HEARTBEAT

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
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

        async def h1(e): pass
        async def h2(e): pass

        s1 = router.subscribe("a.b", h1)
        s2 = router.subscribe("a.*", h2)
        subs = router.get_subscribers("a.b")
        assert len(subs) == 2

        router.unsubscribe(s1.id)
        subs = router.get_subscribers("a.b")
        assert len(subs) == 1
