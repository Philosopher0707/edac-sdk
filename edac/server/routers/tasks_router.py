"""FastAPI router for task endpoints."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status

from edac.server.audit import log_audit
from edac.server.auth import (
    ACTION_GET_EVENTS,
    ACTION_GET_TASK,
    ACTION_LIST_TASKS,
    ACTION_SUBMIT_TASK,
)
from edac.server.schemas import SubmitTaskRequest, TaskResponse
from edac.server.store import TaskStore
from edac.server.worker import QueuedTask, TaskWorker

logger = logging.getLogger("edac.server.routers.tasks")

router = APIRouter()


def _task_to_response(record) -> TaskResponse:
    return TaskResponse(
        id=record.id,
        status=record.status,
        goal=record.goal,
        pattern=record.pattern,
        result=record.result,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.post("/tasks", response_model=TaskResponse)
async def submit_task(req: SubmitTaskRequest, request: Request) -> TaskResponse:
    """Submit a new multi-agent task."""
    app = request.app
    user = getattr(request.state, "user", None)
    if user and not app.state.auth.is_allowed(user, ACTION_SUBMIT_TASK):
        log_audit(ACTION_SUBMIT_TASK, "/tasks", "denied", user=user.name)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    task_id = str(uuid.uuid4())
    store: TaskStore = app.state.store
    worker: TaskWorker = app.state.worker

    await store.create_task(
        task_id=task_id,
        goal=req.goal,
        pattern=req.pattern,
        agents=req.agents,
    )

    await worker.submit(
        QueuedTask(
            task_id=task_id,
            goal=req.goal,
            pattern=req.pattern,
            agents=req.agents,
            max_parallel=req.max_parallel,
        )
    )

    app.state.metrics.counter("tasks_submitted").inc()
    log_audit(ACTION_SUBMIT_TASK, f"/tasks/{task_id}", "success", user=getattr(user, "name", None))

    record = await store.get_task(task_id)
    return _task_to_response(record)


@router.get("/tasks")
async def list_tasks(
    request: Request,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[TaskResponse]:
    """List tasks."""
    app = request.app
    user = getattr(request.state, "user", None)
    if user and not app.state.auth.is_allowed(user, ACTION_LIST_TASKS):
        log_audit(ACTION_LIST_TASKS, "/tasks", "denied", user=user.name)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    store: TaskStore = app.state.store
    records = await store.list_tasks(status=status, limit=limit, offset=offset)
    log_audit(ACTION_LIST_TASKS, "/tasks", "success", user=getattr(user, "name", None))
    return [_task_to_response(r) for r in records]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, request: Request) -> TaskResponse:
    """Get a task by id."""
    app = request.app
    user = getattr(request.state, "user", None)
    if user and not app.state.auth.is_allowed(user, ACTION_GET_TASK):
        log_audit(ACTION_GET_TASK, f"/tasks/{task_id}", "denied", user=user.name)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    store: TaskStore = app.state.store
    record = await store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    log_audit(ACTION_GET_TASK, f"/tasks/{task_id}", "success", user=getattr(user, "name", None))
    return _task_to_response(record)


@router.delete("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> TaskResponse:
    """Cancel a running or queued task."""
    app = request.app
    user = getattr(request.state, "user", None)
    if user and not app.state.auth.is_allowed(user, ACTION_SUBMIT_TASK):
        log_audit(ACTION_SUBMIT_TASK, f"/tasks/{task_id}/cancel", "denied", user=user.name)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    store: TaskStore = app.state.store
    worker: TaskWorker = app.state.worker
    record = await store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if record.status in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Task already {record.status}")

    await worker.cancel_task(task_id)
    log_audit(ACTION_SUBMIT_TASK, f"/tasks/{task_id}/cancel", "success", user=getattr(user, "name", None))
    record = await store.get_task(task_id)
    return _task_to_response(record)


@router.get("/tasks/{task_id}/events")
async def get_task_events(task_id: str, request: Request) -> List[Dict[str, Any]]:
    """Get events for a task."""
    app = request.app
    user = getattr(request.state, "user", None)
    if user and not app.state.auth.is_allowed(user, ACTION_GET_EVENTS):
        log_audit(ACTION_GET_EVENTS, f"/tasks/{task_id}/events", "denied", user=user.name)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    store: TaskStore = app.state.store
    events = await store.get_events(task_id)
    log_audit(ACTION_GET_EVENTS, f"/tasks/{task_id}/events", "success", user=getattr(user, "name", None))
    return events


@router.websocket("/tasks/{task_id}/ws")
async def task_websocket(websocket: WebSocket, task_id: str):
    """WebSocket for real-time task updates.

    Pushes task status changes and new events from the store.
    Client can send ping for keepalive.
    """
    app = websocket.app
    await websocket.accept()
    app.state.websockets.setdefault(task_id, []).append(websocket)
    store: TaskStore = app.state.store
    last_event_id = 0
    last_status: Optional[str] = None
    _running = True

    async def _poll_and_push() -> None:
        nonlocal last_event_id, last_status
        while _running:
            try:
                task = await store.get_task(task_id)
                if task and task.status != last_status:
                    last_status = task.status
                    await websocket.send_json({
                        "type": "task.status",
                        "task_id": task_id,
                        "status": task.status,
                        "result": task.result,
                        "error": task.error,
                        "updated_at": task.updated_at,
                    })

                events = await store.get_events(task_id)
                new_events = [e for e in events if e.get("id", 0) > last_event_id]
                if new_events:
                    last_event_id = max(e.get("id", 0) for e in new_events)
                    for evt in new_events:
                        await websocket.send_json({
                            "type": "task.event",
                            "task_id": task_id,
                            "event_type": evt.get("event_type"),
                            "payload": evt.get("payload"),
                            "timestamp": evt.get("timestamp"),
                        })
            except Exception:
                break
            await asyncio.sleep(0.5)

    poll_task = asyncio.create_task(_poll_and_push())
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        _running = False
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        app.state.websockets.get(task_id, []).remove(websocket)
