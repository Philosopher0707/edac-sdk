"""Tests for memory HTTP endpoints.

Covers LongTerm/Working/Episodic retrieval via the FastAPI
memory_router, plus ContextManager memory integration.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from edac.context.manager import ContextConfig, ContextManager
from edac.event.schema import EventType, create_event
from edac.memory.episodic import EpisodicMemory
from edac.memory.long_term import LongTermMemory
from edac.memory.working import WorkingMemory
from edac.model import ModelRegistry
from edac.server.api import create_app
from edac.server.config import ServerConfig


class TestContextManagerMemory:
    """Unit tests for Memory-layer integration in ContextManager."""

    def test_working_memory_lifecycle(self):
        cm = ContextManager()
        wm = cm.get_working("agent-1")
        wm.write("plan", "search web")
        assert isinstance(wm, WorkingMemory)
        assert cm.get_working("agent-1").read("plan") == "search web"

    def test_long_term_store_and_retrieve(self):
        cm = ContextManager()
        eid = cm.store_long_term("hello world", metadata={"tag": "greeting"})
        entry = cm.get_long_term().get(eid)
        assert entry is not None
        assert entry.content == "hello world"
        assert entry.metadata["tag"] == "greeting"

    def test_long_term_search_text_integration(self):
        cm = ContextManager()
        cm.store_long_term("red apple on table", source="test")
        cm.store_long_term("green banana in bowl", source="test")
        results = cm.get_long_term().search_text("apple")
        assert len(results) == 1
        assert results[0].content == "red apple on table"

    def test_episodic_record_and_retrieve(self):
        cm = ContextManager()
        cid = uuid4()
        event = create_event(
            EventType.AGENT_SPAWN,
            "agent:a",
            "agent.spawn",
            correlation_id=cid,
            payload={"id": "a"},
        )
        cm.record_episode(event)
        traj = cm.get_episodic().get_trajectory(cid)
        assert len(traj) == 1
        assert traj[0].payload["id"] == "a"

    def test_promote_to_long_term(self):
        cm = ContextManager()
        cm.add_to_window("agent-1", "user", "My favourite colour is blue.")
        cm.add_to_window("agent-1", "assistant", "Noted.")
        stored = cm.promote_to_long_term("agent-1")
        assert stored == 1  # only user entry promoted
        results = cm.get_long_term().search_text("colour")
        assert len(results) == 1

    def test_clear_all_memory(self):
        cm = ContextManager(config=ContextConfig(max_tokens_per_agent=100))
        cm.add_to_window("agent-1", "user", "hello")
        cm.get_working("agent-1").write("k", "v")
        cm.store_long_term("fact")
        cm.clear("agent-1")
        assert cm.get_window("agent-1").get_text() == ""
        assert cm.get_working("agent-1").read("k") is None
        # Long-term survives agent-scoped clear
        assert len(cm.get_long_term()._entries) == 1

    def test_stats_include_all_tiers(self):
        cm = ContextManager(config=ContextConfig(max_tokens_per_agent=100))
        cm.get_window("a").add_text("user", "hi")
        cm.get_working("a").write("k", "v")
        cm.store_long_term("fact")
        stats = cm.get_stats()
        assert stats["active_windows"] == 1
        assert stats["active_working"] == 1
        assert stats["long_term_entries"] == 1
        assert stats["episodic_entries"] == 0  # no event recorded


class TestMemoryEndpoints:
    """HTTP integration tests for /memory/* routes."""

    @pytest.fixture
    def client(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            yield client

    def test_list_long_term_empty(self, client):
        response = client.get("/memory/long-term")
        assert response.status_code == 200
        assert response.json() == []

    def test_store_long_term(self, client):
        response = client.post(
            "/memory/long-term",
            json={
                "content": "Paris is the capital of France",
                "metadata": {"region": "Europe"},
                "source": "factbook",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["stored"] is True
        assert "entry_id" in data

        # search for it
        search = client.get("/memory/long-term?query=Paris")
        assert search.status_code == 200
        results = search.json()
        assert len(results) == 1
        assert results[0]["content"] == "Paris is the capital of France"

    def test_delete_long_term(self, client):
        # store
        r = client.post(
            "/memory/long-term",
            json={"content": "temporary fact"},
        )
        eid = r.json()["entry_id"]
        # delete
        d = client.delete(f"/memory/long-term/{eid}")
        assert d.status_code == 200
        assert d.json()["deleted"] is True
        # gone
        assert client.get("/memory/long-term").json() == []

    def test_working_memory_crud(self, client):
        agent_id = "agent-42"
        # write
        r = client.post(
            f"/memory/working/{agent_id}",
            json={"key": "plan", "value": "search web"},
        )
        assert r.status_code == 200
        assert r.json()["stored"] is True
        # read
        r = client.get(f"/memory/working/{agent_id}/plan")
        assert r.status_code == 200
        assert r.json()["value"] == "search web"
        # append
        r = client.post(
            f"/memory/working/{agent_id}",
            json={"append": "then verify"},
        )
        assert r.status_code == 200
        # list
        r = client.get(f"/memory/working/{agent_id}")
        assert r.status_code == 200
        entries = r.json()
        assert len(entries) == 2
        # clear
        r = client.delete(f"/memory/working/{agent_id}")
        assert r.status_code == 200
        assert r.json()["cleared"] is True
        assert client.get(f"/memory/working/{agent_id}").json() == []

    def test_episodic_trajectory(self, client):
        cid = str(uuid4())
        # no events yet
        r = client.get(f"/memory/episodic/{cid}")
        assert r.status_code == 200
        assert r.json() == []

    def test_memory_stats(self, client):
        response = client.get("/memory/stats")
        assert response.status_code == 200
        data = response.json()
        assert "active_windows" in data
        assert "long_term_entries" in data
        assert "episodic_entries" in data
