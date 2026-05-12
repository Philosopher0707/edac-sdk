"""Tests for vector clock distributed causality tracking."""

import asyncio
import pytest
from uuid import uuid4

from edac.event.vector_clock import VectorClock
from edac.event.schema import Event, EventType, create_event
from edac.event.bus import EventBus


class TestVectorClock:
    def test_increment(self):
        vc = VectorClock()
        vc.increment("a")
        assert vc["a"] == 1
        vc.increment("a")
        assert vc["a"] == 2

    def test_increment_returns_self(self):
        vc = VectorClock()
        assert vc.increment("x") is vc

    def test_merge(self):
        vc1 = VectorClock({"a": 3, "b": 1})
        vc2 = VectorClock({"a": 2, "b": 5, "c": 7})
        vc1.merge(vc2)
        assert vc1.as_dict() == {"a": 3, "b": 5, "c": 7}

    def test_merge_returns_self(self):
        vc = VectorClock({"a": 1})
        assert vc.merge(VectorClock({"b": 2})) is vc

    def test_update_only_greater(self):
        vc = VectorClock({"a": 5})
        vc.update("a", 3)  # less than current — no change
        assert vc["a"] == 5
        vc.update("a", 7)  # greater — updates
        assert vc["a"] == 7

    def test_copy_is_independent(self):
        vc = VectorClock({"a": 1})
        c = vc.copy()
        vc.increment("a")
        assert c["a"] == 1
        assert vc["a"] == 2

    def test_from_dict(self):
        d = {"a": 1, "b": 2}
        vc = VectorClock.from_dict(d)
        assert vc.as_dict() == d

    def test_len_and_contains(self):
        vc = VectorClock({"a": 1, "b": 2})
        assert len(vc) == 2
        assert "a" in vc
        assert "z" not in vc

    def test_repr(self):
        vc = VectorClock({"a": 1})
        assert "VectorClock" in repr(vc)
        assert "a" in repr(vc)

    # ── Comparison ──

    def test_compare_equal(self):
        assert VectorClock({"a": 1}).compare(VectorClock({"a": 1})) == 0

    def test_compare_strictly_before(self):
        # {a:1, b:2} strictly before {a:1, b:3}
        assert VectorClock({"a": 1, "b": 2}).compare(VectorClock({"a": 1, "b": 3})) == -2

    def test_compare_strictly_after(self):
        # {a:1, b:3} strictly after {a:1, b:2}
        assert VectorClock({"a": 1, "b": 3}).compare(VectorClock({"a": 1, "b": 2})) == 2

    def test_compare_concurrent(self):
        # {a:1, b:2} concurrent with {a:2, b:1} — neither dominates
        assert VectorClock({"a": 1, "b": 2}).compare(VectorClock({"a": 2, "b": 1})) == 1

    def test_compare_concurrent_disjoint_nodes(self):
        # {a:1} and {b:1} are concurrent (different nodes)
        assert VectorClock({"a": 1}).compare(VectorClock({"b": 1})) == 1

    def test_happens_before(self):
        vc1 = VectorClock({"a": 1})
        vc2 = VectorClock({"a": 2})
        assert vc1.happens_before(vc2)
        assert not vc2.happens_before(vc1)

    def test_happens_after(self):
        vc1 = VectorClock({"a": 1})
        vc2 = VectorClock({"a": 2})
        assert vc2.happens_after(vc1)
        assert not vc1.happens_after(vc2)

    def test_concurrent_with(self):
        vc1 = VectorClock({"a": 1, "b": 2})
        vc2 = VectorClock({"a": 2, "b": 1})
        assert vc1.concurrent_with(vc2)
        assert vc2.concurrent_with(vc1)

    def test_eq(self):
        assert VectorClock({"a": 1}) == VectorClock({"a": 1})
        assert not (VectorClock({"a": 1}) == VectorClock({"a": 2}))
        assert VectorClock({"a": 1}) != "not a clock"

    def test_lt_le_gt_ge(self):
        a = VectorClock({"a": 1})
        b = VectorClock({"a": 2})
        c = VectorClock({"a": 1, "b": 1})
        assert a < b
        assert a <= b
        assert b > a
        assert b >= a
        # {a:1} is strictly before {a:1, b:1} because b=1 > 0
        assert a < c
        assert a <= c
        # c and a are not concurrent — a clearly happens-before c
        assert not a.concurrent_with(c)
        assert c > a
        assert c >= a

    def test_compare_different_nodes(self):
        """Clocks with different node sets compare correctly."""
        vc1 = VectorClock({"a": 1})  # node a sees one event
        vc2 = VectorClock({"a": 1, "b": 1})  # same a, plus b
        assert vc1.happens_before(vc2)
        assert vc2.happens_after(vc1)


class TestEventBusVectorClock:
    @pytest.mark.asyncio
    async def test_emit_stamps_vector_clock(self):
        bus = EventBus()
        async with bus:
            e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
            await bus.emit(e)
            await bus.emit(e)  # second emit
            log = bus.get_event_log()

        # get_event_log returns most-recent first, so reverse to get emit order
        log = list(reversed(bus.get_event_log()))

        assert len(log) == 2
        # First event emitted: local node incremented to 1
        assert log[0].causality_vector[bus.node_id] == 1
        # Second event emitted: local node incremented to 2
        assert log[1].causality_vector[bus.node_id] == 2

    @pytest.mark.asyncio
    async def test_emit_preserves_existing_vector_clock(self):
        bus = EventBus()
        async with bus:
            # Event arrives with existing clock from another node
            existing_vc = {"node-x": 5, "node-y": 3}
            e = create_event(
                EventType.AGENT_SPAWN,
                "agent:a",
                "agent.spawn",
                payload={},
                causality_vector=existing_vc,
            )
            await bus.emit(e)
            log = bus.get_event_log()

        assert len(log) == 1
        stamped = log[0]
        # Merged: node-x=5, node-y=3 preserved; local node incremented
        assert stamped.causality_vector["node-x"] == 5
        assert stamped.causality_vector["node-y"] == 3
        assert stamped.causality_vector[bus.node_id] == 1

    @pytest.mark.asyncio
    async def test_bus_vector_clock_advances(self):
        bus = EventBus()
        async with bus:
            for _ in range(5):
                e = create_event(EventType.SYSTEM_LOG, "system:s", "system.log", payload={})
                await bus.emit(e)

        vc = bus.get_vector_clock()
        assert vc[bus.node_id] == 5

    @pytest.mark.asyncio
    async def test_derive_increments_causality_vector(self):
        """Event.derive() increments the source's entry in the causality vector."""
        parent = create_event(
            EventType.AGENT_SPAWN,
            "agent:planner-1",
            "agent.spawn",
            payload={"goal": "Build API"},
            causality_vector={"planner-1": 3},
        )
        child = parent.derive(
            EventType.AGENT_HEARTBEAT,
            payload={"status": "ok"},
        )
        assert child.causality_vector["planner-1"] == 4
        assert child.parent_event_id == parent.event_id

    @pytest.mark.asyncio
    async def test_event_is_immutable_after_emit(self):
        """Emit creates a stamped copy; original event is unchanged."""
        bus = EventBus()
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})
        original_vc = dict(e.causality_vector)
        async with bus:
            await bus.emit(e)
        # Original event still has empty vector
        assert e.causality_vector == original_vc

    @pytest.mark.asyncio
    async def test_event_model_helpers(self):
        """Event helper methods for vector clock comparison."""
        e1 = create_event(
            EventType.AGENT_SPAWN,
            "agent:a",
            "test",
            payload={},
            causality_vector={"a": 1},
        )
        e2 = create_event(
            EventType.AGENT_HEARTBEAT,
            "agent:a",
            "test",
            payload={},
            causality_vector={"a": 2},
        )
        assert e1.happens_before(e2)
        assert e2.happens_after(e1)
        assert not e1.concurrent_with(e2)

    @pytest.mark.asyncio
    async def test_concurrent_events(self):
        """Two events from different nodes with no causal link are concurrent."""
        e1 = create_event(
            EventType.AGENT_SPAWN,
            "agent:a",
            "test",
            payload={},
            causality_vector={"a": 1},
        )
        e2 = create_event(
            EventType.AGENT_SPAWN,
            "agent:b",
            "test",
            payload={},
            causality_vector={"b": 1},
        )
        assert e1.concurrent_with(e2)
        assert e2.concurrent_with(e1)
        assert not e1.happens_before(e2)
        assert not e2.happens_before(e1)

    @pytest.mark.asyncio
    async def test_handler_chain_preserves_causality(self):
        """When a handler returns a derived event, the vector clock is preserved."""
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
            await bus.emit(e)  # trigger two events
            await bus.emit(e)  # trigger third
            await asyncio.sleep(0.15)
            log = bus.get_event_log()

        # Should have 6 events: 3 emitted + 3 derived
        spawn_events = [ev for ev in log if ev.event_type == EventType.AGENT_SPAWN]
        heartbeat_events = [ev for ev in log if ev.event_type == EventType.AGENT_HEARTBEAT]
        assert len(spawn_events) == 3
        assert len(heartbeat_events) == 3

        # Each derived event should have causality_vector >= parent
        for he in heartbeat_events:
            assert he.causality_vector[bus.node_id] >= 1
            assert "chained" in he.payload

    @pytest.mark.asyncio
    async def test_two_buses_different_node_ids(self):
        """Different EventBus instances have unique node IDs."""
        bus1 = EventBus()
        bus2 = EventBus()
        assert bus1.node_id != bus2.node_id

        async with bus1, bus2:
            e = create_event(EventType.SYSTEM_LOG, "system:s", "log", payload={})
            await bus1.emit(e)
            await bus2.emit(e)

        vc1 = bus1.get_vector_clock()
        vc2 = bus2.get_vector_clock()
        assert vc1[bus1.node_id] == 1
        assert vc2[bus2.node_id] == 1
        # Bus1 should NOT have bus2's node entry and vice versa
        assert bus2.node_id not in vc1
        assert bus1.node_id not in vc2
