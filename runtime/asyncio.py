"""Async Runtime — Structured Concurrency with anyio."""

from __future__ import annotations

import anyio
import anyio.abc
import logging
import signal
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, List, Dict, Any

logger = logging.getLogger("edac.runtime")


@dataclass
class RuntimeConfig:
    """Configuration for the async runtime."""
    max_concurrent_tasks: int = 100
    task_timeout: Optional[float] = None
    graceful_shutdown_timeout: float = 30.0
    enable_signal_handlers: bool = True
    log_task_lifecycle: bool = False


class TaskManager:
    """Manages structured concurrent tasks with cancellation and cleanup."""
    
    def __init__(self, config: RuntimeConfig = None):
        self.config = config or RuntimeConfig()
        self._tasks: Dict[str, anyio.abc.Task] = {}
        self._semaphore: Optional[anyio.Semaphore] = None
        self._shutdown_event: Optional[anyio.Event] = None
    
    async def __aenter__(self) -> TaskManager:
        self._semaphore = anyio.Semaphore(self.config.max_concurrent_tasks)
        self._shutdown_event = anyio.Event()
        
        if self.config.enable_signal_handlers:
            self._setup_signal_handlers()
        
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()
    
    def _setup_signal_handlers(self):
        """Setup graceful shutdown on SIGINT/SIGTERM."""
        def handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            anyio.from_thread.run(self._shutdown_event.set)
        
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
    
    async def spawn(
        self,
        name: str,
        coro: Callable[[], Awaitable[Any]],
        *,
        timeout: Optional[float] = None,
        on_complete: Optional[Callable[[Any, Optional[Exception]], Awaitable[None]]] = None,
    ) -> str:
        """Spawn a managed task with optional timeout and completion callback."""
        task_id = f"task-{name}-{id(coro)}"
        
        async def _wrapped():
            async with self._semaphore:
                if self.config.log_task_lifecycle:
                    logger.debug(f"Task {task_id} started")
                
                try:
                    result = await coro()
                    if on_complete:
                        await on_complete(result, None)
                    return result
                except Exception as e:
                    if on_complete:
                        await on_complete(None, e)
                    raise
                finally:
                    if self.config.log_task_lifecycle:
                        logger.debug(f"Task {task_id} completed")
                    self._tasks.pop(task_id, None)
        
        task = await anyio.create_task_group().start_soon(_wrapped)
        self._tasks[task_id] = task
        return task_id
    
    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task."""
        task = self._tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            return True
        return False
    
    async def cancel_all(self):
        """Cancel all running tasks."""
        for task_id, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
    
    async def shutdown(self):
        """Graceful shutdown with timeout."""
        if self._shutdown_event:
            self._shutdown_event.set()
        
        await self.cancel_all()
        
        # Wait for tasks to finish with timeout
        with anyio.move_on_after(self.config.graceful_shutdown_timeout):
            while self._tasks:
                await anyio.sleep(0.1)
        
        logger.info("Runtime shutdown complete")


@asynccontextmanager
async def runtime(config: RuntimeConfig = None):
    """Context manager for the async runtime."""
    async with TaskManager(config) as tm:
        yield tm
