"""Tests for task queue backends."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from edac.server.queue import AsyncioTaskQueue, RedisTaskQueue, TaskQueue, create_queue


class TestAsyncioTaskQueue:
    @pytest.mark.asyncio
    async def test_put_get(self):
        q = AsyncioTaskQueue()
        await q.put("item1")
        result = await q.get()
        assert result == "item1"
        q.task_done()

    @pytest.mark.asyncio
    async def test_qsize(self):
        q = AsyncioTaskQueue()
        assert q.qsize() == 0
        await q.put("a")
        await q.put("b")
        assert q.qsize() == 2
        await q.get()
        assert q.qsize() == 1

    @pytest.mark.asyncio
    async def test_maxsize_blocks(self):
        q = AsyncioTaskQueue(maxsize=1)
        await q.put("a")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.put("b"), timeout=0.1)

    @pytest.mark.asyncio
    async def test_join(self):
        q = AsyncioTaskQueue()
        await q.put("a")
        await q.put("b")

        async def consumer():
            await q.get()
            q.task_done()
            await q.get()
            q.task_done()

        await asyncio.wait_for(asyncio.gather(q.join(), consumer()), timeout=1.0)


class TestRedisTaskQueue:
    @pytest.mark.asyncio
    async def test_put_get(self):
        mock_redis = AsyncMock()
        mock_redis.rpush = AsyncMock()
        mock_redis.blpop = AsyncMock(side_effect=[("queue", '{"task": 1}'), None])
        mock_redis.llen = AsyncMock(return_value=0)
        mock_redis.close = AsyncMock()

        q = RedisTaskQueue()
        q._redis = mock_redis

        await q.put({"task": 1})
        mock_redis.rpush.assert_awaited_once()
        assert q.qsize() == 1

        result = await q.get()
        assert result == {"task": 1}

    @pytest.mark.asyncio
    async def test_qsize_returns_pending(self):
        q = RedisTaskQueue()
        q._pending = 3
        assert q.qsize() == 3

    @pytest.mark.asyncio
    async def test_task_done_decrements_pending(self):
        q = RedisTaskQueue()
        q._pending = 2
        q.task_done()
        assert q.qsize() == 1
        q.task_done()
        assert q.qsize() == 0
        q.task_done()  # no negative
        assert q.qsize() == 0

    @pytest.mark.asyncio
    async def test_maxsize_rejects_when_full(self):
        mock_redis = AsyncMock()
        mock_redis.llen = AsyncMock(return_value=5)
        mock_redis.close = AsyncMock()

        q = RedisTaskQueue(maxsize=5)
        q._redis = mock_redis

        with pytest.raises(asyncio.QueueFull):
            await q.put("item")

    @pytest.mark.asyncio
    async def test_join_waits_for_empty(self):
        mock_redis = AsyncMock()
        mock_redis.llen = AsyncMock(return_value=0)
        mock_redis.close = AsyncMock()

        q = RedisTaskQueue()
        q._redis = mock_redis
        q._pending = 0

        await asyncio.wait_for(q.join(), timeout=0.2)

    @pytest.mark.asyncio
    async def test_close(self):
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock()
        q = RedisTaskQueue()
        q._redis = mock_redis
        await q.close()
        mock_redis.close.assert_awaited_once()
        assert q._redis is None


class TestCreateQueue:
    def test_asyncio_backend(self):
        q = create_queue(backend="asyncio")
        assert isinstance(q, AsyncioTaskQueue)

    def test_redis_backend(self):
        q = create_queue(backend="redis")
        assert isinstance(q, RedisTaskQueue)

    def test_unknown_backend(self):
        with pytest.raises(ValueError, match="Unknown queue backend"):
            create_queue(backend="kafka")
