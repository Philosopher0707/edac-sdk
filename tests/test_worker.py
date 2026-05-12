"""Tests for TaskWorker."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from edac.server.executor import AgentExecutor
from edac.server.store import TaskStore
from edac.server.worker import QueuedTask, TaskWorker


class TestTaskWorkerRetry:
    @pytest.mark.asyncio
    async def test_max_retries_zero_executes_once(self):
        """max_retries=0 should still execute the task once, not skip it."""
        store = TaskStore(database_url="sqlite+aiosqlite:///:memory:")
        await store.connect()

        executor = AsyncMock(spec=AgentExecutor)
        executor.execute = AsyncMock(
            return_value=AsyncMock(success=True, artifacts=[], agent_results={})
        )

        worker = TaskWorker(
            store=store,
            executor=executor,
            max_retries=0,
            backend="asyncio",
        )
        await worker.start()
        await store.create_task(task_id="t1", goal="Test", pattern="pipeline", agents=[])

        task = QueuedTask(
            task_id="t1",
            goal="Test",
            pattern="pipeline",
            agents=[{"name": "a"}],
        )
        await worker.submit(task)
        await asyncio.wait_for(worker.queue.join(), timeout=2.0)
        await worker.stop()

        executor.execute.assert_awaited_once()
        fetched = await store.get_task("t1")
        assert fetched.status == "completed"
        await store.close()

    @pytest.mark.asyncio
    async def test_max_retries_zero_dlq_immediate(self):
        """max_retries=0 + failure should move to DLQ immediately."""
        store = TaskStore(database_url="sqlite+aiosqlite:///:memory:")
        await store.connect()

        executor = AsyncMock(spec=AgentExecutor)
        executor.execute = AsyncMock(side_effect=RuntimeError("boom"))

        worker = TaskWorker(
            store=store,
            executor=executor,
            max_retries=0,
            backend="asyncio",
        )
        await worker.start()

        task = QueuedTask(
            task_id="t1",
            goal="Test",
            pattern="pipeline",
            agents=[{"name": "a"}],
        )
        await store.create_task(task_id="t1", goal="Test", pattern="pipeline", agents=[])
        await worker.submit(task)
        await asyncio.wait_for(worker.queue.join(), timeout=2.0)
        await worker.stop()

        dlq = await store.list_dlq()
        assert len(dlq) == 1
        assert dlq[0]["task_id"] == "t1"
        assert dlq[0]["error"] == "boom"
        await store.close()
