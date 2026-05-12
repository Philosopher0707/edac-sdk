"""Tests for EDAC chat — ChatStore, ChatAgent, and router."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from edac.chat.agent import ChatAgent
from edac.chat.store import ChatMessage, ChatSession, ChatStore
from edac.model import ChatCompletion
from edac.server.api import create_app
from edac.server.config import ServerConfig


# ── ChatStore Tests ──


class TestChatStore:
    """Unit tests for the in-memory chat session store."""

    def test_create_session(self) -> None:
        store = ChatStore()
        session = store.create_session(
            agent_id="test-agent",
            title="Test Chat",
            provider="ollama",
            model="llama3.2",
        )

        assert isinstance(session.session_id, str)
        assert session.agent_id == "test-agent"
        assert session.title == "Test Chat"
        assert session.provider == "ollama"
        assert session.model == "llama3.2"
        assert session.status == "active"
        assert session.messages == []

    def test_get_and_list_sessions(self) -> None:
        store = ChatStore()
        s1 = store.create_session(agent_id="a1")
        s2 = store.create_session(agent_id="a2", status="paused")

        assert store.get_session(s1.session_id) == s1
        assert store.get_session(s2.session_id) == s2

        sessions = store.list_sessions()
        assert len(sessions) == 2

        active_only = store.list_sessions(status="active")
        assert len(active_only) == 1
        assert active_only[0].status == "active"

    def test_update_and_close(self) -> None:
        store = ChatStore()
        session = store.create_session(agent_id="a1")
        sid = session.session_id

        store.update_session(sid, title="Updated")
        assert store.get_session(sid).title == "Updated"

        store.update_session(sid, status="closed")
        assert store.get_session(sid).status == "closed"

    def test_add_and_get_messages(self) -> None:
        store = ChatStore()
        session = store.create_session(agent_id="a1")
        sid = session.session_id

        store.add_message(sid, "user", "Hello")
        store.add_message(sid, "assistant", "Hi there!")

        msgs = store.get_messages(sid)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "Hello"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "Hi there!"

    def test_get_messages_pagination(self) -> None:
        store = ChatStore()
        session = store.create_session(agent_id="a1")
        sid = session.session_id

        for i in range(10):
            store.add_message(sid, "user", f"msg {i}")

        # last 5
        msgs = store.get_messages(sid, limit=5)
        assert len(msgs) == 5
        assert msgs[-1].content == "msg 4"

        # skip 8, get 2
        msgs = store.get_messages(sid, offset=8, limit=5)
        assert len(msgs) == 2
        assert msgs[0].content == "msg 8"

    def test_delete_session(self) -> None:
        store = ChatStore()
        session = store.create_session(agent_id="a1")
        sid = session.session_id

        assert store.delete_session(sid) is True
        assert store.get_session(sid) is None
        assert store.delete_session(sid) is False

    def test_session_not_found(self) -> None:
        store = ChatStore()
        fake_id = str(uuid.uuid4())
        assert store.get_session(fake_id) is None
        assert store.get_messages(fake_id) == []
        assert store.delete_session(fake_id) is False


# ── Chat Router Tests ──


class TestChatRouter:
    """Integration tests for the FastAPI chat endpoints."""

    @pytest.fixture
    def client(self) -> TestClient:
        cfg = ServerConfig(database_url="sqlite+aiosqlite:///:memory:", api_key=None)
        app = create_app(config=cfg)
        with TestClient(app) as client:
            yield client

    def _parse_uuid(self, text: str) -> uuid.UUID:
        data = json.loads(text)
        return uuid.UUID(data["session_id"])

    def test_create_session(self, client: TestClient) -> None:
        resp = client.post(
            "/chat/sessions",
            json={"model": "llama3.2", "title": "Test"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test"
        assert "session_id" in data
        assert data["model"] == "llama3.2"
        assert data["provider"] == "ollama"

    def test_list_sessions(self, client: TestClient) -> None:
        client.post("/chat/sessions", json={"model": "llama3.2"})
        client.post("/chat/sessions", json={"model": "llama3.2"})

        resp = client.get("/chat/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2

    def test_get_session(self, client: TestClient) -> None:
        created = client.post("/chat/sessions", json={"model": "llama3.2"}).json()
        sid = created["session_id"]

        resp = client.get(f"/chat/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == sid

    def test_delete_session(self, client: TestClient) -> None:
        created = client.post("/chat/sessions", json={"model": "llama3.2"}).json()
        sid = created["session_id"]

        resp = client.delete(f"/chat/sessions/{sid}")
        assert resp.status_code == 204

        resp = client.get(f"/chat/sessions/{sid}")
        assert resp.status_code == 404

    def test_send_message_stores_history(self, client: TestClient) -> None:
        created = client.post("/chat/sessions", json={"model": "llama3.2"}).json()
        sid = created["session_id"]

        with patch(
            "edac.server.routers.chat_router._get_registry"
        ) as mock_get_registry:
            mock_registry = MagicMock()
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=ChatCompletion(content="Hello from mock")
            )
            mock_registry.get.return_value = mock_provider
            mock_get_registry.return_value = mock_registry

            resp = client.post(
                f"/chat/sessions/{sid}/send",
                json={"message": "How are you?"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) >= 2
            assert any(m["role"] == "assistant" for m in data)
            assert any(
                "Hello from mock" in m["content"] for m in data
                if m["role"] == "assistant"
            )

        # Verify history
        resp = client.get(f"/chat/sessions/{sid}")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        assert len(msgs) >= 2
        assert msgs[0]["role"] == "user"
        assert msgs[-1]["role"] == "assistant"

    def test_get_history_not_found(self, client: TestClient) -> None:
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/chat/sessions/{fake_id}")
        assert resp.status_code == 404


# ── Chat Agent Tests ──


class TestChatAgentUnit:
    """Unit tests for ChatAgent logic."""

    @pytest.mark.asyncio
    async def test_agent_factory_subscribes_and_processes(self) -> None:
        bus = AsyncMock()
        registry = MagicMock()
        registry.get.return_value = None
        registry.get_default = AsyncMock(return_value=None)

        ctx = MagicMock()
        ctx.get_window.return_value = MagicMock()
        ctx.get_window.return_value.get_window.return_value = []

        agent_instance = MagicMock()
        agent_instance.state.agent_id = "test"
        agent_instance.state.state = "running"

        agent = ChatAgent(
            agent_instance=agent_instance,
            session_id=str(uuid.uuid4()),
            bus=bus,
            registry=registry,
            ctx=ctx,
        )

        # run() should subscribe and enter the main loop
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(agent.run(), timeout=0.1)

        # verify subscription
        bus.subscribe.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_input_streams_response(self) -> None:
        bus = AsyncMock()

        mock_chunks = [
            MagicMock(content="Hello", finish_reason=None, usage=None),
            MagicMock(content=" world", finish_reason=None, usage=None),
            MagicMock(content="", finish_reason="stop", usage={"total_tokens": 5}),
        ]

        async def mock_stream(*args, **kwargs):
            for c in mock_chunks:
                yield c

        mock_registry = MagicMock()
        mock_registry.stream = mock_stream
        mock_registry.get.return_value = mock_registry
        mock_registry.get_default = AsyncMock(return_value=mock_registry)

        ctx = MagicMock()
        ctx.get_window.return_value = MagicMock()
        ctx.get_window.return_value.get_window.return_value = []

        agent_instance = MagicMock()
        agent_instance.state.agent_id = "test"

        agent = ChatAgent(
            agent_instance=agent_instance,
            session_id=str(uuid.uuid4()),
            bus=bus,
            registry=mock_registry,
            ctx=ctx,
        )

        input_event = MagicMock(
            source="human:alice",
            correlation_id=uuid.uuid4(),
            payload={"text": "Hi"},
        )

        await agent._on_input(input_event)

        # Should emit at least two token events + one done event
        calls = bus.emit.await_args_list
        assert len(calls) >= 3


# ── SqliteChatStore Tests ──


class TestSqliteChatStore:
    @pytest.mark.asyncio
    async def test_persist_and_resume(self, tmp_path):
        """Messages survive a store close + reopen."""
        from edac.chat.sqlite_store import SqliteChatStore
        import os

        db_path = os.path.join(tmp_path, "test_chat.db")

        # Create + populate
        store = SqliteChatStore(db_path=db_path)
        await store.connect()
        session = store.create_session(agent_id="a1", title="Test")
        await store.persist_session(session)
        store.add_message(session.session_id, "user", "Hello")
        store.add_message(session.session_id, "assistant", "Hi!")
        # Give background SQLite writes time to flush
        await asyncio.sleep(0.1)
        await store.close()

        # Reopen and verify
        store2 = SqliteChatStore(db_path=db_path)
        await store2.connect()
        loaded = await store2.load_from_db()
        assert loaded == 1

        s = store2.get_session(session.session_id)
        assert s is not None
        assert s.title == "Test"

        msgs = store2.get_messages(session.session_id)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "Hello"
        assert msgs[1].role == "assistant"

        await store2.close()
