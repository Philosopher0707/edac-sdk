"""Background worker — processes tasks from an async queue.

Runs continuously, executing tasks via AgentExecutor.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from edac.server.executor import AgentExecutor
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
    ):
        self.store = store
        self.executor = executor
        self.queue: asyncio.Queue[QueuedTask] = asyncio.Queue(maxsize=maxsize)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the worker loop."""
        self._running = True
        self._task = asyncio.create_task(self._worker_loop(), name="task_worker")
        logger.info("TaskWorker started")

    async def stop(self) -> None:
        """Stop the worker loop."""
        self._running = False
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

    async def _worker_loop(self) -> None:
        """Process tasks from the queue."""
        while self._running:
            try:
                task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                await self._process(task)
            except Exception as e:
                logger.exception(f"Failed to process task {task.task_id}: {e}")
                await self.store.update_task_status(
                    task.task_id,
                    status="failed",
                    error=str(e),
                )
            finally:
                self.queue.task_done()

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
        result = await self.executor.execute(
            goal=task.goal,
            agents=task.agents,
            pattern=task.pattern,
            max_parallel=task.max_parallel,
        )

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
