"""FastAPI router for system endpoints (health, metrics, dlq)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from edac.model import ModelRegistry
from edac.server.audit import log_audit
from edac.server.auth import ACTION_GET_HEALTH, ACTION_GET_METRICS
from edac.server.schemas import HealthResponse
from edac.server.store import TaskStore
from edac.server.worker import TaskWorker

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Deep health check — verifies DB, worker, model registry."""
    app = request.app
    components: Dict[str, Any] = {}
    overall = "healthy"

    # Database
    try:
        store: TaskStore = app.state.store
        await store.list_tasks(limit=1)
        components["database"] = {"status": "ok"}
    except Exception as e:
        components["database"] = {"status": "error", "detail": str(e)}
        overall = "degraded"

    # Worker
    worker: TaskWorker = app.state.worker
    components["worker"] = {
        "status": "ok" if worker._running else "stopped",
        "current_task": worker._current_task.task_id if worker._current_task else None,
        "queue_size": worker.queue.qsize(),
    }

    # Model registry
    registry: ModelRegistry = app.state.registry
    try:
        models = await registry.list_models()
        components["model_registry"] = {"status": "ok", "models": models}
    except Exception as e:
        components["model_registry"] = {"status": "error", "detail": str(e)}
        overall = "degraded"

    # Circuit breakers
    for name in ["ollama", "anthropic", "openai"]:
        cb = getattr(app.state, f"cb_{name}", None)
        if cb:
            components[f"circuit_{name}"] = {"status": cb.state.value}

    log_audit(ACTION_GET_HEALTH, "/health", "success")
    return HealthResponse(
        status=overall,
        version="0.3.2",
        components=components,
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> str:
    """Prometheus-compatible metrics."""
    app = request.app
    user = getattr(request.state, "user", None)
    if user and not app.state.auth.is_allowed(user, ACTION_GET_METRICS):
        log_audit(ACTION_GET_METRICS, "/metrics", "denied", user=user.name)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    mc = app.state.metrics
    log_audit(ACTION_GET_METRICS, "/metrics", "success", user=getattr(user, "name", None))
    return mc.export()


@router.get("/dlq")
async def list_dlq(
    request: Request,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List dead letter queue entries with pagination."""
    app = request.app
    store: TaskStore = app.state.store
    records = await store.list_dlq(limit=limit, offset=offset)
    total = await store.count_dlq()

    from fastapi.responses import JSONResponse

    return JSONResponse(
        content=records,
        headers={
            "X-Total-Count": str(total),
            "X-Limit": str(limit),
            "X-Offset": str(offset),
        },
    )
