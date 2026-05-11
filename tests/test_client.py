"""Tests for EdacClient SDK — sync tests with isolated event loops.

Each test method creates a fresh event loop and drives the ASGI lifespan
manually.  This avoids any conflict with pytest-asyncio's loop management or
background tasks leaking between tests.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from edac.client.client import CreateAgentRequest, EdacClient
from edac.client.exceptions import EdacAPIError, EdacAuthError, EdacNotFoundError
from edac.server.api import create_app
from edac.server.auth import Role
from edac.server.config import ServerConfig


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
# 1. Health (raw httpx — no SDK)
# =============================================================================

class TestEdacClientHealth:
    def test_get_health_ok(self):
        app = _mk_app()
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.get("/health", timeout=5)

        resp = _run_with_lifespan(app, _req)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")
        assert data["version"] == "0.2.0"
        assert "components" in data

    def test_health_has_request_id(self):
        app = _mk_app()
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.get("/health", headers={"x-request-id": "abc123"}, timeout=5)

        resp = _run_with_lifespan(app, _req)
        assert resp.headers.get("x-request-id") == "abc123"


# =============================================================================
# 2. Auth (raw httpx)
# =============================================================================

class TestEdacClientAuth:
    def test_401_without_api_key(self):
        app = _mk_app(api_key="secret")
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.get("/agents", timeout=5)

        resp = _run_with_lifespan(app, _req)
        assert resp.status_code == 401

    def test_403_insufficient_role(self):
        # api_key="dummy" forces the auth middleware to be installed
        app = _mk_app(api_key="dummy")
        transport = httpx.ASGITransport(app=app)

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")
            app.state.auth.register("viewer_key", Role.VIEWER, name="Viewer")

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.post(
                    "/agents",
                    json={"name": "a"},
                    headers={"x-api-key": "viewer_key"},
                    timeout=5,
                )

        resp = _run_with_lifespan(app, _req, setup=_setup)
        assert resp.status_code == 403

    def test_200_with_valid_key(self):
        app = _mk_app(api_key="dummy")
        transport = httpx.ASGITransport(app=app)

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.get("/agents", headers={"x-api-key": "admin_key"}, timeout=5)

        resp = _run_with_lifespan(app, _req, setup=_setup)
        assert resp.status_code == 200


# =============================================================================
# 3. CRUD (raw httpx)
# =============================================================================

def _admin_setup(app):
    """Post-startup hook: register an admin key."""
    app.state.auth.register("admin_key", Role.ADMIN, name="Admin")


class TestEdacClientCRUD:
    def test_list_agents_empty(self):
        app = _mk_app()
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.get("/agents", headers={"x-api-key": "admin_key"}, timeout=5)

        resp = _run_with_lifespan(app, _req, setup=_admin_setup)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_agent(self):
        app = _mk_app()
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.post(
                    "/agents",
                    json={"name": "test-agent", "agent_type": "test"},
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )

        resp = _run_with_lifespan(app, _req, setup=_admin_setup)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-agent"
        assert data["agent_type"] == "test"
        assert "agent_id" in data

    def test_get_agent(self):
        app = _mk_app()
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                r1 = await c.post(
                    "/agents",
                    json={"name": "a1"},
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )
                agent_id = r1.json()["agent_id"]
                return await c.get(
                    f"/agents/{agent_id}",
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )

        resp = _run_with_lifespan(app, _req, setup=_admin_setup)
        assert resp.status_code == 200
        assert resp.json()["name"] == "a1"

    def test_get_agent_404(self):
        app = _mk_app()
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.get(
                    "/agents/no-such-id",
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )

        resp = _run_with_lifespan(app, _req, setup=_admin_setup)
        assert resp.status_code == 404

    def test_delete_agent(self):
        app = _mk_app()
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                r1 = await c.post(
                    "/agents",
                    json={"name": "a1"},
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )
                agent_id = r1.json()["agent_id"]
                return await c.delete(
                    f"/agents/{agent_id}",
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )

        resp = _run_with_lifespan(app, _req, setup=_admin_setup)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_pause_and_resume_agent(self):
        app = _mk_app()
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                r1 = await c.post(
                    "/agents",
                    json={"name": "a1"},
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )
                agent_id = r1.json()["agent_id"]
                r2 = await c.post(
                    f"/agents/{agent_id}/pause",
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )
                assert r2.json()["state"] == "paused"
                r3 = await c.post(
                    f"/agents/{agent_id}/resume",
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )
                assert r3.json()["state"] == "idle"
                return True

        assert _run_with_lifespan(app, _req, setup=_admin_setup) is True

    def test_restart_agent(self):
        app = _mk_app()
        transport = httpx.ASGITransport(app=app)

        async def _req():
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                r1 = await c.post(
                    "/agents",
                    json={"name": "a1"},
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )
                old_id = r1.json()["agent_id"]
                r2 = await c.post(
                    f"/agents/{old_id}/restart",
                    headers={"x-api-key": "admin_key"},
                    timeout=5,
                )
                new_id = r2.json()["agent_id"]
                assert new_id != old_id
                return True

        assert _run_with_lifespan(app, _req, setup=_admin_setup) is True


# =============================================================================
# 4. EdacClient SDK
# =============================================================================

class TestEdacClientSDK:
    def test_sdk_create_and_list(self):
        app = _mk_app(api_key="dummy")

        def _setup(app):
            app.state.auth.register("admin_key", Role.ADMIN, name="Admin")

        async def _flow(client: EdacClient):
            created = await client.create_agent(CreateAgentRequest(name="sdk-agent"))
            assert created.name == "sdk-agent"
            agents = await client.list_agents()
            assert any(a.agent_id == created.agent_id for a in agents)
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
