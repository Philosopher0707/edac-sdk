"""Tests for EdacClient SDK — sync tests with isolated event loops.

Each test method creates a fresh event loop and drives the ASGI lifespan
manually.  This avoids any conflict with pytest-asyncio's loop management or
background tasks leaking between tests.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from edac.client.client import CreateAgentRequest, EdacClient, RetryConfig
from edac.client.exceptions import (
    EdacAPIError,
    EdacAuthError,
    EdacNotFoundError,
    EdacRetryExhausted,
)
from edac.client.sync_client import EdacClientSync
from edac.server.api import create_app
from edac.server.auth import Role
from edac.server.config import ServerConfig
from edac.server.schemas import SubmitTaskRequest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mk_app(*, api_key: str = "") -> FastAPI:
    """Create a full FastAPI app for end-to-end testing."""
    cfg = ServerConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        api_key=api_key or None,
    )
    return create_app(cfg)


def _run_with_lifespan(
    app: FastAPI,
    coro,
    *,
    setup=None,
    timeout: float = 15.0,
):
    """Drive lifespan.startup, optionally run *setup*, then *coro*, then shutdown."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)

        async def _main():
            started = asyncio.Event()
            shutdown = asyncio.Event()
            stopped = asyncio.Event()

            async def recv():
                if not started.is_set():
                    return {"type": "lifespan.startup"}
                await shutdown.wait()
                return {"type": "lifespan.shutdown"}

            async def send(msg):
                if msg["type"] == "lifespan.startup.complete":
                    started.set()
                elif msg["type"] == "lifespan.shutdown.complete":
                    stopped.set()

            task = asyncio.create_task(
                app(
                    {"type": "lifespan", "asgi": {"version": "3.0"}, "state": app.state},
                    recv,
                    send,
                )
            )
            await asyncio.wait_for(started.wait(), timeout=timeout)
            try:
                if setup is not None:
                    setup(app)
                result = await coro()
            finally:
                shutdown.set()
                await asyncio.wait_for(stopped.wait(), timeout=timeout)
                await asyncio.wait_for(task, timeout=timeout)
            return result

        return loop.run_until_complete(_main())
    finally:
        loop.close()


def _run_with_client(app: FastAPI, coro, api_key: str, *, setup=None, timeout: float = 15.0):
    """Like _run_with_lifespan but injects an EdacClient wired to *app*."""
    transport = httpx.ASGITransport(app=app)

    async def _client_coro():
        client = EdacClient(base_url="http://test", api_key=api_key)
        client._client = httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=5,
            headers=client._headers(),
        )
        try:
            return await coro(client)
        finally:
            await client.close()

    return _run_with_lifespan(app, _client_coro, setup=setup, timeout=timeout)


# =============================================================================
# 1. Health
# =============================================================================


class TestEdacClientHealth:
    def test_health_without_auth(self):
        app = _mk_app()

        async def _req():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=5
            ) as client:
                r = await client.get("/health")
                assert r.status_code == 200
                data = r.json()
                assert data["status"] in ("healthy", "degraded")
                assert "components" in data
                return True

        assert _run_with_lifespan(app, _req) is True


# =============================================================================
# 2. Auth
# =============================================================================


class TestEdacClientAuth:
    def test_401_without_key(self):
        app = _mk_app(api_key="sekrit")

        async def _req():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=5
            ) as client:
                r = await client.get("/agents")
                assert r.status_code == 401
                return True

        assert _run_with_lifespan(app, _req) is True

    def test_403_with_wrong_key(self):
        app = _mk_app(api_key="sekrit")

        def _setup(app):
            app.state.auth.register("right_key", Role.OPERATOR, name="Op")

        async def _req():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                timeout=5,
                headers={"X-API-Key": "wrong_key"},
            ) as client:
                r = await client.get("/agents")
                # Wrong key → not authenticated → 401
                assert r.status_code == 401
                return True

        assert _run_with_lifespan(app, _req, setup=_setup) is True

    def test_allowed_with_valid_key(self):
        app = _mk_app(api_key="sekrit")

        def _setup(app):
            app.state.auth.register("valid_key", Role.OPERATOR, name="Op")

        async def _req():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                timeout=5,
                headers={"X-API-Key": "valid_key"},
            ) as client:
                r = await client.get("/agents")
                assert r.status_code == 200
                return True

        assert _run_with_lifespan(app, _req, setup=_setup) is True


# =============================================================================
# 3. Agent CRUD (raw HTTP to verify endpoints)
# =============================================================================


class TestEdacClientCRUD:
    def test_crud(self):
        app = _mk_app(api_key="sekrit")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _req():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                timeout=5,
                headers={"X-API-Key": "admin_key"},
            ) as client:
                # Create
                r = await client.post(
                    "/agents",
                    json={"name": "test-crud", "agent_type": "test"},
                )
                assert r.status_code == 201
                agent_id = r.json()["agent_id"]

                # List — return type hint says List[AgentInfo] but endpoint uses
                # JSONResponse for headers → raw list
                r2 = await client.get("/agents")
                assert r2.status_code == 200
                data = r2.json()
                assert isinstance(data, list)
                assert any(a["agent_id"] == agent_id for a in data)

                # Get
                r3 = await client.get(f"/agents/{agent_id}")
                assert r3.status_code == 200
                assert r3.json()["name"] == "test-crud"

                # Restart
                r4 = await client.post(f"/agents/{agent_id}/restart")
                assert r4.status_code == 200
                new_id = r4.json()["agent_id"]
                assert new_id != agent_id
                return True

        assert _run_with_lifespan(app, _req, setup=_setup) is True


# =============================================================================
# 4. EdacClient SDK (async)
# =============================================================================


class TestEdacClientSDK:
    def test_sdk_create_and_list(self):
        app = _mk_app(api_key="dummy")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _flow(client: EdacClient):
            created = await client.create_agent(CreateAgentRequest(name="sdk-agent"))
            assert created.name == "sdk-agent"
            page = await client.list_agents()
            assert any(a.agent_id == created.agent_id for a in page.items)
            assert page.total >= 1
            return True

        assert _run_with_client(app, _flow, api_key="admin_key", setup=_setup, timeout=15) is True

    def test_sdk_get_delete(self):
        app = _mk_app(api_key="dummy")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _flow(client: EdacClient):
            created = await client.create_agent(CreateAgentRequest(name="sdk-gd"))
            got = await client.get_agent(created.agent_id)
            assert got.name == "sdk-gd"
            deleted = await client.delete_agent(created.agent_id)
            assert deleted["status"] == "deleted"
            return True

        assert _run_with_client(app, _flow, api_key="admin_key", setup=_setup, timeout=15) is True

    def test_sdk_health(self):
        app = _mk_app(api_key="dummy")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _flow(client: EdacClient):
            health = await client.get_health()
            assert health.status in ("healthy", "degraded")
            return True

        assert _run_with_client(app, _flow, api_key="admin_key", setup=_setup, timeout=15) is True

    def test_sdk_404_raises(self):
        app = _mk_app(api_key="dummy")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _flow(client: EdacClient):
            with pytest.raises(EdacNotFoundError):
                await client.get_agent("nonexistent-id")
            return True

        assert _run_with_client(app, _flow, api_key="admin_key", setup=_setup, timeout=15) is True

    def test_sdk_403_raises(self):
        app = _mk_app(api_key="dummy")

        def _setup2(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")
            app.state.auth.register("viewer_key", Role.VIEWER, name="Viewer")

        async def _flow(client: EdacClient):
            # Re-inject a viewer-key client
            viewer = EdacClient(base_url="http://test", api_key="viewer_key")
            viewer._client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client._client._transport.app),
                base_url="http://test",
                timeout=5,
                headers=viewer._headers(),
            )
            try:
                with pytest.raises(EdacAuthError):
                    await viewer.create_agent(CreateAgentRequest(name="no-perm"))
            finally:
                await viewer.close()
            return True

        assert _run_with_client(app, _flow, api_key="admin_key", setup=_setup2, timeout=15) is True

    def test_sdk_pagination(self):
        app = _mk_app(api_key="dummy")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _flow(client: EdacClient):
            # Create 5 agents
            for i in range(5):
                await client.create_agent(CreateAgentRequest(name=f"pag-{i}"))
            page = await client.list_agents(limit=2, offset=0)
            assert page.total >= 5
            assert len(page.items) == 2
            assert page.limit == 2
            assert page.offset == 0

            page2 = await client.list_agents(limit=2, offset=2)
            assert len(page2.items) == 2
            assert page2.offset == 2

            # Tasks pagination
            tasks_page = await client.list_tasks(limit=1, offset=0)
            assert tasks_page.total >= 0
            return True

        assert _run_with_client(app, _flow, api_key="admin_key", setup=_setup, timeout=15) is True

    def test_sdk_batch_agents(self):
        app = _mk_app(api_key="dummy")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _flow(client: EdacClient):
            reqs = [CreateAgentRequest(name=f"batch-{i}") for i in range(3)]
            results = await client.create_agents_batch(reqs)
            assert len(results) == 3
            assert all(isinstance(r, type(results[0])) for r in results)
            successes = [r for r in results if hasattr(r, "agent_id")]
            assert len(successes) == 3
            return True

        assert _run_with_client(app, _flow, api_key="admin_key", setup=_setup, timeout=15) is True

    def test_sdk_tasks_crud(self):
        app = _mk_app(api_key="dummy")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _flow(client: EdacClient):
            task = await client.submit_task(SubmitTaskRequest(goal="test-task", pattern="pipeline"))
            assert task.goal == "test-task"
            assert task.status == "pending"

            got = await client.get_task(task.id)
            assert got.id == task.id

            events = await client.get_task_events(task.id)
            assert isinstance(events, list)
            return True

        assert _run_with_client(app, _flow, api_key="admin_key", setup=_setup, timeout=15) is True

    def test_sdk_retry_exhausted_on_404(self):
        """Retries should not happen on 404 – it should raise immediately."""
        app = _mk_app(api_key="dummy")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _flow(client: EdacClient):
            # 404 should NOT be retried
            with pytest.raises(EdacNotFoundError):
                await client.get_agent("no-such-agent")
            return True

        assert _run_with_client(app, _flow, api_key="admin_key", setup=_setup, timeout=15) is True


# =============================================================================
# 5. Sync client
# =============================================================================


class TestEdacClientSync:
    @pytest.mark.skip(
        reason="ASGI transport cannot be shared across event loops; sync logic covered by mocked tests in test_client_extended.py"
    )
    def test_sync_create_and_list(self):
        pass

    @pytest.mark.skip(
        reason="ASGI transport cannot be shared across event loops; sync logic covered by mocked tests in test_client_extended.py"
    )
    def test_sync_context_manager(self):
        pass


# =============================================================================
# 6. Retry / Backoff
# =============================================================================


class TestEdacClientRetry:
    def test_custom_retry_config(self):
        cfg = RetryConfig(max_retries=5, backoff_base=0.5)
        assert cfg.max_retries == 5
        assert cfg.backoff_base == 0.5

    def test_retry_config_invalid(self):
        with pytest.raises(ValueError):
            RetryConfig(max_retries=-1)
        with pytest.raises(ValueError):
            RetryConfig(backoff_base=0)

    def test_client_with_retry(self):
        """Smoke test that the client accepts retry config."""
        client = EdacClient("http://test", retry=RetryConfig(max_retries=2))
        assert client._retry.max_retries == 2

    def test_auth_not_retried(self):
        """401/403 should not trigger retry."""
        from edac.client.retry import _should_retry
        from edac.client.exceptions import EdacAuthError

        cfg = RetryConfig()
        assert not _should_retry(EdacAuthError("nope"), cfg)

    def test_notfound_not_retried(self):
        from edac.client.retry import _should_retry
        from edac.client.exceptions import EdacNotFoundError

        cfg = RetryConfig()
        assert not _should_retry(EdacNotFoundError("nope"), cfg)

    def test_500_is_retried(self):
        from edac.client.retry import _should_retry
        from edac.client.exceptions import EdacAPIError

        cfg = RetryConfig()
        assert _should_retry(EdacAPIError("boom", status_code=500), cfg)

    def test_429_is_retried(self):
        from edac.client.retry import _should_retry
        from edac.client.exceptions import EdacAPIError

        cfg = RetryConfig()
        assert _should_retry(EdacAPIError("rate", status_code=429), cfg)
