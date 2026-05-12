"""Task queue abstraction — asyncio default, Redis-ready interface.

Queue interface:
  put(item)      — enqueue
  get()          — dequeue (blocks)
  task_done()    — mark item processed
  qsize()        — current size
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

logger = logging.getLogger("edac.server.queue")

T = TypeVar("T")


class TaskQueue(ABC, Generic[T]):
    """Abstract task queue."""

    @abstractmethod
    async def put(self, item: T) -> None: ...

    @abstractmethod
    async def get(self) -> T: ...

    @abstractmethod
    def task_done(self) -> None: ...

    @abstractmethod
    def qsize(self) -> int: ...

    @abstractmethod
    async def join(self) -> None: ...


class AsyncioTaskQueue(TaskQueue[T]):
    """In-memory asyncio queue."""

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: T) -> None:
        await self._queue.put(item)

    async def get(self) -> T:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    async def join(self) -> None:
        await self._queue.join()


class RedisTaskQueue(TaskQueue[T]):
    """Redis-backed task queue using a list.

    Uses RPUSH to enqueue and BLPOP to dequeue.
    Items are JSON-serialized.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        queue_name: str = "edac:tasks",
        maxsize: int = 0,
    ) -> None:
        self.redis_url = redis_url
        self.queue_name = queue_name
        self.maxsize = maxsize
        self._pending = 0
        self._lock = asyncio.Lock()
        self._redis: Optional[Any] = None

    async def _get_redis(self) -> Any:
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
            except ImportError as e:
                raise RuntimeError(
                    "redis package required for Redis queue. Install: pip install redis"
                ) from e
            self._redis = await aioredis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def put(self, item: T) -> None:
        r = await self._get_redis()
        if self.maxsize > 0:
            length = await r.llen(self.queue_name)
            if length >= self.maxsize:
                raise asyncio.QueueFull()
        payload = json.dumps(item, default=str)
        await r.rpush(self.queue_name, payload)
        async with self._lock:
            self._pending += 1

    async def get(self) -> T:
        r = await self._get_redis()
        while True:
            result = await r.blpop(self.queue_name, timeout=1)
            if result is not None:
                _, payload = result
                return json.loads(payload)
            await asyncio.sleep(0.1)

    def task_done(self) -> None:
        self._pending = max(0, self._pending - 1)

    def qsize(self) -> int:
        # qsize can't be async in the interface; return last known count
        # This is a best-effort estimate for health checks
        return self._pending

    async def join(self) -> None:
        r = await self._get_redis()
        while True:
            length = await r.llen(self.queue_name)
            async with self._lock:
                if length == 0 and self._pending == 0:
                    break
            await asyncio.sleep(0.1)

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None


def create_queue(
    maxsize: int = 0,
    backend: str = "asyncio",
    redis_url: str = "redis://localhost:6379",
    queue_name: str = "edac:tasks",
) -> TaskQueue[Any]:
    """Factory for task queues."""
    if backend == "asyncio":
        return AsyncioTaskQueue(maxsize=maxsize)
    if backend == "redis":
        return RedisTaskQueue(
            redis_url=redis_url,
            queue_name=queue_name,
            maxsize=maxsize,
        )
    raise ValueError(f"Unknown queue backend: {backend}")
