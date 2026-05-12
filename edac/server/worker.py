"""Background worker — processes tasks from an async queue.

Runs continuously, executing tasks via AgentExecutor.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from edac.server.executor import AgentExecutor
from edac.server.queue import AsyncioTaskQueue, TaskQueue, create_queue
from edac.server.retry import retry
from edac.server.store import TaskStore

logger = logging.getLogger("edac.server.worker")


@dataclass
class QueuedTask:
    """A task waiting to be processed."""

    task_id: str
    goal: str
    pattern: str
    agents: List[Dict[str, Any]]
    max_parallel: int = 3


class TaskWorker:
    """Background worker that processes tasks from a queue."""

    def __init__(
        self,
        store: TaskStore,
        executor: AgentExecutor,
        maxsize: int = 1000,
        backend: str = "asyncio",
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 60.0,
        metrics: Optional[Any] = None,
    ):
        self.store = store
        self.executor = executor
        self.queue: TaskQueue[QueuedTask] = create_queue(maxsize=maxsize, backend=backend)
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay
        self.metrics = metrics
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._current_task: Optional[QueuedTask] = None
        self._current_process: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start the worker loop."""
        self._running = True
        self._shutdown_event.clear()
        self._task = asyncio.create_task(self._worker_loop(), name="task_worker")
        logger.info("TaskWorker started")

    async def stop(self) -> None:
        """Graceful shutdown — finish current task, drain queue."""
        logger.info("TaskWorker shutting down gracefully...")
        self._running = False

        # Wait for queue to drain (with timeout)
        try:
            await asyncio.wait_for(self.queue.join(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("TaskWorker shutdown timeout — some tasks may be lost")

        # Cancel worker loop
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("TaskWorker stopped")

    async def submit(self, task: QueuedTask) -> None:
        """Submit a task to the queue."""
        await self.queue.put(task)
        logger.debug(f"Task {task.task_id} queued")

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running or queued task.

        Returns True if the task was found and cancelled.
        """
        # If currently processing, cancel the asyncio task
        if self._current_task and self._current_task.task_id == task_id:
            if self._current_process and not self._current_process.done():
                self._current_process.cancel()
                logger.info(f"Cancelling running task {task_id}")
                return True
        # If in queue, we can't easily remove from asyncio.Queue without draining.
        # Mark as cancelled in store so worker skips it when it pops.
        # For now, update store status and let it fail fast when picked up.
        await self.store.update_task_status(task_id, status="cancelled")
        await self.store.add_event(task_id, "task.cancelled", {"reason": "user_request"})
        logger.info(f"Marked task {task_id} as cancelled")
        return True

    async def _worker_loop(self) -> None:
        """Process tasks from the queue."""
        while self._running:
            if self.metrics:
                self.metrics.gauge("task_queue_size").set(self.queue.qsize())
            try:
                task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            self._current_task = task
            self._current_process = asyncio.create_task(self._process_with_retry(task))
            try:
                await self._current_process
            except asyncio.CancelledError:
                logger.info(f"Task {task.task_id} cancelled")
                await self.store.update_task_status(
                    task.task_id,
                    status="cancelled",
                    error="Cancelled by user",
                )
                await self.store.add_event(
                    task.task_id,
                    "task.cancelled",
                    {"reason": "user_request"},
                )
            except Exception as e:
                logger.exception(f"Failed to process task {task.task_id}: {e}")
                await self.store.update_task_status(
                    task.task_id,
                    status="failed",
                    error=str(e),
                )
                moved = await self.store.move_to_dlq(task.task_id, max_retries=0)
                if moved:
                    logger.warning(f"Task {task.task_id} moved to DLQ")
            finally:
                self._current_process = None
                self._current_task = None
                self.queue.task_done()

    async def _process_with_retry(self, task: QueuedTask) -> None:
        """Process with retry and DLQ fallback."""
        last_err: Optional[Exception] = None
        max_attempts = max(1, self.max_retries)
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._process(task)
            except Exception as e:
                last_err = e
                if attempt >= self.max_retries:
                    break
                delay = min(
                    self.retry_base_delay * (2 ** (attempt - 1)),
                    self.retry_max_delay,
                )
                logger.warning(
                    f"Task {task.task_id} attempt {attempt} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)

        if last_err:
            raise last_err

    async def _process(self, task: QueuedTask) -> None:
        """Process a single task."""
        logger.info(f"Processing task {task.task_id}: {task.goal}")

        # Update status to in_progress
        await self.store.update_task_status(task.task_id, status="in_progress")
        await self.store.add_event(
            task.task_id,
            "task.started",
            {"goal": task.goal, "pattern": task.pattern},
        )

        # Execute
        start = time.monotonic()
        result = await self.executor.execute(
            goal=task.goal,
            agents=task.agents,
            pattern=task.pattern,
            max_parallel=task.max_parallel,
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        if self.metrics:
            self.metrics.histogram("task_execution_duration_ms").observe(elapsed_ms)
            self.metrics.counter("tasks_processed").inc()
            if result.success:
                self.metrics.counter("tasks_completed").inc()
            else:
                self.metrics.counter("tasks_failed").inc()

        # Persist result
        if result.success:
            await self.store.update_task_status(
                task.task_id,
                status="completed",
                result={
                    "success": result.success,
                    "artifacts": result.artifacts,
                    "agent_results": result.agent_results,
                },
            )
        else:
            await self.store.update_task_status(
                task.task_id,
                status="failed",
                result={
                    "success": False,
                    "artifacts": result.artifacts,
                    "agent_results": result.agent_results,
                },
            )

        await self.store.add_event(
            task.task_id,
            "task.completed" if result.success else "task.failed",
            {"success": result.success},
        )

        logger.info(f"Task {task.task_id} {'completed' if result.success else 'failed'}")
