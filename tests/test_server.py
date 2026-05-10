"""Tests for the EDAC production server."""

import pytest
from fastapi.testclient import TestClient

from edac.server.api import create_app
from edac.server.config import ServerConfig
from edac.server.store import TaskStore


class TestServerHealth:
    def test_health_endpoint(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] in ("healthy", "degraded")
            assert data["version"] == "0.2.0"
            assert "components" in data
            assert "database" in data["components"]
            assert "worker" in data["components"]

    def test_health_has_request_id(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/health", headers={"x-request-id": "abc123"})
            assert response.status_code == 200
            assert response.headers.get("x-request-id") == "abc123"


class TestServerTasks:
    def test_submit_task(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.post("/tasks", json={
                "goal": "Build a REST API",
                "pattern": "pipeline",
                "agents": [
                    {"name": "planner", "role": "orchestrator"},
                    {"name": "coder", "role": "worker"},
                ],
                "max_parallel": 2,
            })
            assert response.status_code == 200
            data = response.json()
            assert data["goal"] == "Build a REST API"
            assert data["status"] == "pending"
            assert "id" in data

    def test_list_tasks(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            # Submit a task
            client.post("/tasks", json={"goal": "Test task", "pattern": "pipeline", "agents": []})

            response = client.get("/tasks")
            assert response.status_code == 200
            data = response.json()
            assert len(data) >= 1
            assert data[0]["goal"] == "Test task"

    def test_get_task_not_found(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/tasks/nonexistent")
            assert response.status_code == 404

    def test_submit_invalid_pattern(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.post("/tasks", json={
                "goal": "Test",
                "pattern": "invalid",
                "agents": [],
            })
            assert response.status_code == 422

    def test_get_task_events(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            # Submit and get task
            resp = client.post("/tasks", json={"goal": "G1", "pattern": "pipeline", "agents": []})
            task_id = resp.json()["id"]

            response = client.get(f"/tasks/{task_id}/events")
            assert response.status_code == 200
            assert isinstance(response.json(), list)


class TestServerAgents:
    def test_list_agents(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/agents")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)


class TestServerMetrics:
    def test_metrics_endpoint(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/metrics")
            assert response.status_code == 200
            text = response.text
            assert "tasks_submitted" in text or text == ""
            assert response.headers["content-type"].startswith("text/plain")


class TestServerDLQ:
    def test_dlq_empty(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/dlq")
            assert response.status_code == 200
            data = response.json()
            assert data == []


class TestServerRateLimiting:
    def test_rate_limit_not_triggered(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


class TestTaskStore:
    @pytest.mark.asyncio
    async def test_create_and_get_task(self):
        store = TaskStore(database_url="sqlite+aiosqlite:///:memory:")
        await store.connect()
        try:
            task = await store.create_task(
                task_id="t1",
                goal="Build API",
                pattern="pipeline",
                agents=[{"name": "coder"}],
            )
            assert task.id == "t1"
            assert task.status == "pending"
            assert task.goal == "Build API"

            fetched = await store.get_task("t1")
            assert fetched is not None
            assert fetched.id == "t1"
            assert fetched.goal == "Build API"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_update_task_status(self):
        store = TaskStore(database_url="sqlite+aiosqlite:///:memory:")
        await store.connect()
        try:
            await store.create_task(task_id="t1", goal="Build API")
            await store.update_task_status(
                "t1",
                status="completed",
                result={"output": "done"},
            )

            task = await store.get_task("t1")
            assert task.status == "completed"
            assert task.result == {"output": "done"}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_list_tasks_with_filter(self):
        store = TaskStore(database_url="sqlite+aiosqlite:///:memory:")
        await store.connect()
        try:
            await store.create_task(task_id="t1", goal="G1")
            await store.create_task(task_id="t2", goal="G2")
            await store.update_task_status("t1", status="completed")

            all_tasks = await store.list_tasks()
            assert len(all_tasks) == 2

            completed = await store.list_tasks(status="completed")
            assert len(completed) == 1
            assert completed[0].id == "t1"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_events(self):
        store = TaskStore(database_url="sqlite+aiosqlite:///:memory:")
        await store.connect()
        try:
            await store.create_task(task_id="t1", goal="Build API")
            await store.add_event("t1", "task.started", {"msg": "hello"})
            await store.add_event("t1", "task.completed", {"msg": "done"})

            events = await store.get_events("t1")
            assert len(events) == 2
            assert events[0]["event_type"] == "task.started"
            assert events[1]["event_type"] == "task.completed"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_dlq(self):
        store = TaskStore(database_url="sqlite+aiosqlite:///:memory:")
        await store.connect()
        try:
            await store.create_task(task_id="t1", goal="Build API")
            await store.update_task_status("t1", status="failed", error="boom")

            moved = await store.move_to_dlq("t1", max_retries=0)
            assert moved is True

            dlq = await store.list_dlq()
            assert len(dlq) == 1
            assert dlq[0]["task_id"] == "t1"
            assert dlq[0]["error"] == "boom"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_increment_retry(self):
        store = TaskStore(database_url="sqlite+aiosqlite:///:memory:")
        await store.connect()
        try:
            await store.create_task(task_id="t1", goal="Build API")
            count = await store.increment_retry("t1")
            assert count == 1
            count = await store.increment_retry("t1")
            assert count == 2
        finally:
            await store.close()


class TestServerAuth:
    def test_rbac_viewer_cannot_submit_task(self):
        from edac.server.auth import Role
        cfg = ServerConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            api_key="admin-key",
        )
        app = create_app(config=cfg)
        with TestClient(app) as client:
            app.state.auth.register("viewer-key", Role.VIEWER, name="viewer")
            resp = client.post(
                "/tasks",
                json={"goal": "Build API", "pattern": "pipeline", "agents": []},
                headers={"x-api-key": "viewer-key"},
            )
            assert resp.status_code == 403
            assert resp.json()["detail"] == "Permission denied"

    def test_rbac_admin_can_submit_task(self):
        from edac.server.auth import Role
        cfg = ServerConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            api_key="admin-key",
        )
        app = create_app(config=cfg)
        with TestClient(app) as client:
            app.state.auth.register("admin-key", Role.ADMIN, name="admin")
            resp = client.post(
                "/tasks",
                json={"goal": "Build API", "pattern": "pipeline", "agents": []},
                headers={"x-api-key": "admin-key"},
            )
            assert resp.status_code == 200

    def test_rbac_unauthenticated_blocked(self):
        cfg = ServerConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            api_key="admin-key",
        )
        app = create_app(config=cfg)
        with TestClient(app) as client:
            resp = client.post(
                "/tasks",
                json={"goal": "Build API", "pattern": "pipeline", "agents": []},
            )
            assert resp.status_code == 401


class TestExecutorConfig:
    def test_find_agent_config_from_context(self):
        from edac.server.executor import AgentExecutor

        executor = AgentExecutor(
            bus=None,  # type: ignore
            runtime=None,  # type: ignore
            registry=None,  # type: ignore
            ctx_manager=None,  # type: ignore
        )
        context = {
            "agents": [
                {"name": "planner", "model": "claude-sonnet", "provider": "anthropic"},
                {"name": "coder", "model": "gpt-4o", "provider": "openai"},
            ],
            "goal": "Build API",
        }
        cfg = executor._find_agent_config("coder", context)
        assert cfg["model"] == "gpt-4o"
        assert cfg["provider"] == "openai"

    def test_find_agent_config_fallback(self):
        from edac.server.executor import AgentExecutor

        executor = AgentExecutor(
            bus=None,  # type: ignore
            runtime=None,  # type: ignore
            registry=None,  # type: ignore
            ctx_manager=None,  # type: ignore
        )
        cfg = executor._find_agent_config("unknown", {"goal": "x"})
        assert cfg == {"name": "unknown"}


class TestExecutorCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self):
        from edac.server.executor import AgentExecutor
        from edac.server.circuit_breaker import CircuitBreaker
        from edac.model import ChatCompletion, ChatMessage, ModelProvider, ModelRegistry
        from edac.context.manager import ContextManager, ContextConfig

        class FailingProvider(ModelProvider):
            def __init__(self):
                self.calls = 0

            @property
            def name(self):
                return "fail"

            def is_available(self):
                return True

            async def chat(self, messages, **kwargs):
                self.calls += 1
                raise RuntimeError("provider down")

            async def stream(self, messages, **kwargs):
                pass

            async def close(self):
                pass

            async def list_models(self):
                return []

        registry = ModelRegistry()
        fail = FailingProvider()
        registry.register("fail", fail)
        ctx = ContextManager(registry=registry, config=ContextConfig(default_provider="fail"))
        cb = CircuitBreaker("fail", failure_threshold=2, recovery_timeout=60.0)
        executor = AgentExecutor(
            bus=None,  # type: ignore
            runtime=None,  # type: ignore
            registry=registry,
            ctx_manager=ctx,
            circuit_breakers={"fail": cb},
        )

        ctx = {"goal": "g", "agents": [{"name": "agent", "provider": "fail"}]}

        # First call: provider fails, circuit records failure
        r1 = await executor._call_llm("agent", "a1", ctx)
        assert r1["status"] == "failed"
        assert fail.calls == 1

        # Second call: provider fails, circuit opens
        r2 = await executor._call_llm("agent", "a1", ctx)
        assert r2["status"] == "failed"
        assert fail.calls == 2
        assert cb.state.value == "open"

        # Third call: circuit breaker open, no provider call
        r3 = await executor._call_llm("agent", "a1", ctx)
        assert r3["status"] == "failed"
        assert "Circuit breaker open" in r3["error"]
        assert fail.calls == 2  # no additional provider call
