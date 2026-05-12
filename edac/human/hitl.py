"""Human-in-the-Loop State Machine.

States:
    submitted → working → [input-required → working]* → completed
                       ↓                              ↓
                  [notifications]                [failed]

Emits events for every state transition.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

from edac.event.schema import Event, EventType, EventPriority, create_event

logger = logging.getLogger("edac.human.hitl")


class HITLState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class HITLTask:
    """A task tracked through the HITL state machine."""

    task_id: str
    goal: str
    state: HITLState = HITLState.SUBMITTED
    history: List[Dict[str, Any]] = field(default_factory=list)
    result: Any = None
    error: Optional[str] = None


class HITLStateMachine:
    """Manages HITL tasks and state transitions."""

    def __init__(
        self, on_state_change: Optional[Callable[[HITLTask], Coroutine[Any, Any, None]]] = None
    ):
        self._tasks: Dict[str, HITLTask] = {}
        self._handlers: Dict[str, asyncio.Event] = {}
        self.on_state_change = on_state_change

    def create_task(self, task_id: str, goal: str) -> HITLTask:
        task = HITLTask(task_id=task_id, goal=goal)
        self._tasks[task_id] = task
        self._handlers[task_id] = asyncio.Event()
        logger.info(f"HITL task created: {task_id}")
        return task

    def get_task(self, task_id: str) -> Optional[HITLTask]:
        return self._tasks.get(task_id)

    def transition(self, task_id: str, new_state: HITLState) -> Optional[HITLTask]:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        old = task.state
        task.state = new_state
        task.history.append({"from": old, "to": new_state})
        logger.info(f"HITL task {task_id}: {old.value} → {new_state.value}")

        if new_state == HITLState.INPUT_REQUIRED:
            self._handlers[task_id].clear()

        if self.on_state_change:
            asyncio.create_task(self.on_state_change(task))
        return task

    async def request_input(
        self, task_id: str, prompt: str, timeout: Optional[float] = None
    ) -> Optional[str]:
        """Block until human provides input."""
        task = self._tasks.get(task_id)
        if task is None:
            return None

        self.transition(task_id, HITLState.INPUT_REQUIRED)

        try:
            await asyncio.wait_for(self._handlers[task_id].wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"HITL input timeout for {task_id}")
            return None

        # Result stored by provide_input
        return task.result

    def provide_input(self, task_id: str, response: Any) -> bool:
        """Resume a task waiting for human input."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.result = response
        self._handlers[task_id].set()
        self.transition(task_id, HITLState.WORKING)
        return True

    def complete(self, task_id: str, result: Any) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.result = result
            self.transition(task_id, HITLState.COMPLETED)

    def fail(self, task_id: str, error: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.error = error
            self.transition(task_id, HITLState.FAILED)
