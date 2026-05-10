"""Task queue abstraction — asyncio default, Redis-ready interface.

Queue interface:
  put(item)      — enqueue
  get()          — dequeue (blocks)
  task_done()    — mark item processed
  qsize()        — current size
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

logger = logging.getLogger("edac.server.queue")

T = TypeVar("T")


class TaskQueue(ABC, Generic[T]):
    """Abstract task queue."""

    @abstractmethod
    async def put(self, item: T) -> None:
        ...

    @abstractmethod
    async def get(self) -> T:
        ...

    @abstractmethod
    def task_done(self) -> None:
        ...

    @abstractmethod
    def qsize(self) -> int:
        ...

    @abstractmethod
    async def join(self) -> None:
        ...


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


def create_queue(maxsize: int = 0, backend: str = "asyncio") -> TaskQueue[Any]:
    """Factory for task queues."""
    if backend == "asyncio":
        return AsyncioTaskQueue(maxsize=maxsize)
    raise ValueError(f"Unknown queue backend: {backend}")
