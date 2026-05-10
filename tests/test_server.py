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
            assert data["status"] == "healthy"
            assert data["version"] == "0.2.0"


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
