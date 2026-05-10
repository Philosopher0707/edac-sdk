"""Tests for task store backends."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from edac.server.store import TaskRecord, TaskStore, create_store


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


class TestCreateStore:
    def test_sqlite_backend(self):
        store = create_store("sqlite+aiosqlite:///:memory:")
        assert isinstance(store, TaskStore)

    def test_postgres_backend(self):
        from edac.server.store import PostgresTaskStore

        store = create_store("postgresql://localhost/edac")
        assert isinstance(store, PostgresTaskStore)

    def test_postgres_backend_alt_prefix(self):
        from edac.server.store import PostgresTaskStore

        store = create_store("postgres://localhost/edac")
        assert isinstance(store, PostgresTaskStore)

    def test_unknown_backend(self):
        with pytest.raises(ValueError, match="Unsupported database URL"):
            create_store("mysql://localhost/edac")


class TestPostgresTaskStore:
    @pytest.mark.asyncio
    async def test_connect_migrates_tables(self):
        from edac.server.store import PostgresTaskStore

        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool):
            store = PostgresTaskStore("postgresql://localhost/edac")
            await store.connect()

        assert store._pool is mock_pool
        mock_pool.close = AsyncMock()
        await store.close()

    @pytest.mark.asyncio
    async def test_create_task(self):
        from edac.server.store import PostgresTaskStore

        mock_conn = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        store = PostgresTaskStore("postgresql://localhost/edac")
        store._pool = mock_pool

        task = await store.create_task(task_id="t1", goal="G1", agents=[{"name": "a"}])
        assert task.id == "t1"
        assert task.goal == "G1"
        mock_conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_task(self):
        from edac.server.store import PostgresTaskStore

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": "t1",
            "status": "pending",
            "goal": "G1",
            "pattern": "pipeline",
            "agents": "[]",
            "result": None,
            "error": None,
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        })
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        store = PostgresTaskStore("postgresql://localhost/edac")
        store._pool = mock_pool

        task = await store.get_task("t1")
        assert task is not None
        assert task.id == "t1"
        mock_conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_tasks(self):
        from edac.server.store import PostgresTaskStore

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{
            "id": "t1",
            "status": "completed",
            "goal": "G1",
            "pattern": "pipeline",
            "agents": "[{\"name\": \"a\"}]",
            "result": "{\"ok\": true}",
            "error": None,
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }])
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        store = PostgresTaskStore("postgresql://localhost/edac")
        store._pool = mock_pool

        tasks = await store.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].id == "t1"
        assert tasks[0].agents == [{"name": "a"}]
        assert tasks[0].result == {"ok": True}

    @pytest.mark.asyncio
    async def test_move_to_dlq(self):
        from edac.server.store import PostgresTaskStore

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": "t1",
            "status": "failed",
            "goal": "G1",
            "pattern": "pipeline",
            "agents": "[]",
            "result": None,
            "error": "boom",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
            "retry_count": 0,
        })
        mock_conn.execute = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        store = PostgresTaskStore("postgresql://localhost/edac")
        store._pool = mock_pool

        moved = await store.move_to_dlq("t1", max_retries=0)
        assert moved is True
