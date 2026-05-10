"""Tests for memory layers."""

import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from edac.memory.working import WorkingMemory
from edac.memory.short_term import ShortTermMemory, WindowEntry
from edac.memory.long_term import LongTermMemory, MemoryEntry
from edac.memory.episodic import EpisodicMemory
from edac.event.schema import Event, EventType, create_event


class TestWorkingMemory:
    def test_write_and_read(self):
        mem = WorkingMemory("agent-1")
        mem.write("key", "value")
        assert mem.read("key") == "value"
        assert mem.read("missing") is None

    def test_trim(self):
        mem = WorkingMemory("agent-1", max_entries=2)
        mem.write("a", 1)
        mem.write("b", 2)
        mem.write("c", 3)
        assert len(mem.entries) == 2

    def test_clear(self):
        mem = WorkingMemory("agent-1")
        mem.write("x", 1)
        mem.clear()
        assert len(mem.entries) == 0


class TestShortTermMemory:
    def test_add_and_window(self):
        mem = ShortTermMemory(max_tokens=100)
        mem.add_text("user", "hello")
        assert len(mem.get_window()) == 1
        assert "hello" in mem.get_text()

    def test_budget_enforcement(self):
        mem = ShortTermMemory(max_tokens=20, reserved_tokens=0)
        mem.add_text("user", "a" * 100)  # ~25 tokens
        mem.add_text("user", "b" * 100)
        assert mem.total_tokens() <= 20

    def test_estimate_tokens(self):
        assert ShortTermMemory._estimate_tokens("abcd") == 1
        assert ShortTermMemory._estimate_tokens("a" * 400) == 100


class TestLongTermMemory:
    def test_store_and_get(self):
        mem = LongTermMemory()
        eid = mem.store("hello world", source="test")
        entry = mem.get(eid)
        assert entry is not None
        assert entry.content == "hello world"

    def test_search_text(self):
        mem = LongTermMemory()
        mem.store("hello world")
        mem.store("goodbye world")
        results = mem.search_text("hello")
        assert len(results) == 1
        assert results[0].content == "hello world"

    def test_cosine_similarity(self):
        mem = LongTermMemory()
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert mem._cosine_sim(a, b) == 0.0
        assert mem._cosine_sim(a, a) == 1.0

    def test_delete(self):
        mem = LongTermMemory()
        eid = mem.store("x")
        assert mem.delete(eid)
        assert mem.get(eid) is None


class TestEpisodicMemory:
    def test_append_and_trajectory(self):
        mem = EpisodicMemory()
        cid = uuid4()
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", correlation_id=cid, payload={})
        mem.append(e)
        traj = mem.get_trajectory(cid)
        assert len(traj) == 1

    def test_replay_order(self):
        mem = EpisodicMemory()
        cid = uuid4()
        e1 = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", correlation_id=cid, payload={})
        e2 = create_event(EventType.AGENT_HEARTBEAT, "agent:a", "agent.health", correlation_id=cid, payload={})
        mem.append(e1)
        mem.append(e2)
        replayed = mem.replay(cid)
        assert replayed[0].event_type == EventType.AGENT_SPAWN
        assert replayed[1].event_type == EventType.AGENT_HEARTBEAT

    def test_export(self, tmp_path):
        mem = EpisodicMemory()
        cid = uuid4()
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", correlation_id=cid, payload={})
        mem.append(e)
        out = tmp_path / "export.jsonl"
        mem.export(cid, out)
        assert out.exists()
        lines = out.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_persistence(self, tmp_path):
        path = tmp_path / "events.jsonl"
        cid = uuid4()
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", correlation_id=cid, payload={})

        mem1 = EpisodicMemory(path)
        mem1.append(e)

        mem2 = EpisodicMemory(path)
        traj = mem2.get_trajectory(cid)
        assert len(traj) == 1
