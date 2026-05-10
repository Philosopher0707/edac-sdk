"""FastAPI HTTP API for the EDAC production server.

Endpoints:
  POST /tasks              — submit a new task
  GET  /tasks              — list tasks
  GET  /tasks/{id}         — get task by id
  GET  /tasks/{id}/events  — get task events
  GET  /tasks/{id}/ws      — WebSocket for real-time updates
  GET  /agents             — list agents
  GET  /health             — health check (deep)
  GET  /metrics            — Prometheus metrics
  GET  /dlq                — dead letter queue
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextManager
from edac.event.bus import EventBus
from edac.model import ModelRegistry
from edac.model.anthropic import AnthropicProvider
from edac.model.ollama import OllamaProvider
from edac.model.openai import OpenAIProvider
from edac.observability.metrics import MetricsCollector
from edac.server.audit import log_audit
from edac.server.auth import (
    ACTION_GET_EVENTS,
    ACTION_GET_HEALTH,
    ACTION_GET_METRICS,
    ACTION_GET_TASK,
    ACTION_LIST_AGENTS,
    ACTION_LIST_TASKS,
    ACTION_SUBMIT_TASK,
    AuthManager,
    Role,
)
from edac.server.circuit_breaker import CircuitBreaker
from edac.server.config import ServerConfig
from edac.server.executor import AgentExecutor
from edac.server.rate_limiter import RateLimiter
from edac.server.store import TaskStore
from edac.server.tracing import clear_request_id, get_request_id, set_request_id
from edac.server.worker import QueuedTask, TaskWorker

logger = logging.getLogger("edac.server.api")

# ── Pydantic Models ──


class SubmitTaskRequest(BaseModel):
    """Request to submit a new task."""

    goal: str = Field(..., min_length=1, max_length=10000)
    pattern: str = Field(default="pipeline", pattern="^(pipeline|mesh|orchestrator-workers)$")
    agents: List[Dict[str, Any]] = Field(default_factory=list)
    max_parallel: int = Field(default=3, ge=1, le=20)


class TaskResponse(BaseModel):
    """Task response."""

    id: str
    status: str
    goal: str
    pattern: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str


class AgentInfo(BaseModel):
    """Agent information."""

    name: str
    agent_type: str
    state: str
    model: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    components: Dict[str, Any]


# ── FastAPI App ──


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown."""
    cfg: ServerConfig = app.state.config

    # Database
    store = TaskStore(database_url=cfg.database_url)
    await store.connect()
    app.state.store = store

    # Event bus + runtime
    bus = EventBus()
    await bus.start()
    runtime = AgentRuntime(bus)
    await runtime.start()

    # Model registry
    registry = ModelRegistry()
    ollama = OllamaProvider(base_url=cfg.ollama_base_url, default_model=cfg.ollama_default_model)
    registry.register("ollama", ollama, fallback=True)

    # Circuit breakers per provider
    cb_ollama = CircuitBreaker("ollama", failure_threshold=cfg.circuit_breaker_threshold, recovery_timeout=cfg.circuit_breaker_recovery)
    app.state.cb_ollama = cb_ollama

    anthropic = None
    if cfg.anthropic_api_key:
        anthropic = AnthropicProvider(
            api_key=cfg.anthropic_api_key,
            default_model=cfg.anthropic_default_model,
        )
        registry.register("anthropic", anthropic)
        app.state.cb_anthropic = CircuitBreaker("anthropic", failure_threshold=cfg.circuit_breaker_threshold, recovery_timeout=cfg.circuit_breaker_recovery)

    openai = None
    if cfg.openai_api_key:
        openai = OpenAIProvider(
            api_key=cfg.openai_api_key,
            default_model=cfg.openai_default_model,
            base_url=cfg.openai_base_url,
        )
        registry.register("openai", openai)
        app.state.cb_openai = CircuitBreaker("openai", failure_threshold=cfg.circuit_breaker_threshold, recovery_timeout=cfg.circuit_breaker_recovery)

    # Context manager
    ctx = ContextManager(registry=registry)

    # Executor + worker
    executor = AgentExecutor(bus, runtime, registry, ctx)
    worker = TaskWorker(
        store=store,
        executor=executor,
        maxsize=cfg.task_queue_maxsize,
        backend=cfg.queue_backend,
        max_retries=cfg.retry_max_attempts,
        retry_base_delay=cfg.retry_base_delay,
        retry_max_delay=cfg.retry_max_delay,
    )
    await worker.start()

    # Auth + rate limiting
    auth = AuthManager()
    if cfg.api_key:
        auth.register(cfg.api_key, Role.ADMIN, name="default_admin")
    rate_limiter = RateLimiter(
        default_capacity=cfg.rate_limit_capacity,
        default_refill=cfg.rate_limit_refill,
    )

    app.state.bus = bus
    app.state.runtime = runtime
    app.state.registry = registry
    app.state.ctx = ctx
    app.state.executor = executor
    app.state.worker = worker
    app.state.metrics = MetricsCollector()
    app.state.auth = auth
    app.state.rate_limiter = rate_limiter
    app.state.websockets: Dict[str, List[WebSocket]] = {}

    logger.info("EDAC server started")
    yield

    # Shutdown
    logger.info("EDAC server shutting down...")
    await worker.stop()
    await runtime.stop()
    await bus.stop()
    await store.close()
    await ollama.close()
    if anthropic:
        await anthropic.close()
    if openai:
        await openai.close()
    logger.info("EDAC server stopped")


def create_app(config: Optional[ServerConfig] = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = config or ServerConfig()

    app = FastAPI(
        title="EDAC",
        description="Event-Driven Agentic Core — Production API",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.config = cfg

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Tracing middleware — inject correlation ID
    @app.middleware("http")
    async def tracing_middleware(request: Request, call_next):
        rid = request.headers.get("x-request-id")
        set_request_id(rid)
        response = await call_next(request)
        response.headers["x-request-id"] = get_request_id() or ""
        clear_request_id()
        return response

    # Rate limiting middleware
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        rate_limiter: RateLimiter = app.state.rate_limiter
        client_id = request.headers.get("x-api-key", request.client.host if request.client else "anonymous")
        if not await rate_limiter.acquire(client_id):
            wait = await rate_limiter.wait_time(client_id)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": f"Rate limit exceeded. Retry after {wait:.1f}s"},
                headers={"Retry-After": str(int(wait) + 1)},
            )
        return await call_next(request)

    # Auth middleware
    if cfg.api_key:
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            # Skip auth for health endpoint
            if request.url.path == "/health":
                return await call_next(request)

            key = request.headers.get("x-api-key")
            auth: AuthManager = app.state.auth
            user = auth.authenticate(key or "")
            if user is None:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid or missing API key"},
                )
            request.state.user = user
            return await call_next(request)

    # ── Routes ──

    @app.post("/tasks", response_model=TaskResponse)
    async def submit_task(req: SubmitTaskRequest) -> TaskResponse:
        """Submit a new multi-agent task."""
        # RBAC check
        user = getattr(getattr(Request, "state", None), "user", None)
        if user and not app.state.auth.is_allowed(user, ACTION_SUBMIT_TASK):
            log_audit(ACTION_SUBMIT_TASK, "/tasks", "denied", user=user.name)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

        task_id = str(uuid.uuid4())
        store: TaskStore = app.state.store
        worker: TaskWorker = app.state.worker

        # Persist
        await store.create_task(
            task_id=task_id,
            goal=req.goal,
            pattern=req.pattern,
            agents=req.agents,
        )

        # Queue
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

    @app.get("/tasks")
    async def list_tasks(
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TaskResponse]:
        """List tasks."""
        user = getattr(getattr(Request, "state", None), "user", None)
        if user and not app.state.auth.is_allowed(user, ACTION_LIST_TASKS):
            log_audit(ACTION_LIST_TASKS, "/tasks", "denied", user=user.name)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

        store: TaskStore = app.state.store
        records = await store.list_tasks(status=status, limit=limit, offset=offset)
        log_audit(ACTION_LIST_TASKS, "/tasks", "success", user=getattr(user, "name", None))
        return [_task_to_response(r) for r in records]

    @app.get("/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str) -> TaskResponse:
        """Get a task by id."""
        user = getattr(getattr(Request, "state", None), "user", None)
        if user and not app.state.auth.is_allowed(user, ACTION_GET_TASK):
            log_audit(ACTION_GET_TASK, f"/tasks/{task_id}", "denied", user=user.name)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

        store: TaskStore = app.state.store
        record = await store.get_task(task_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        log_audit(ACTION_GET_TASK, f"/tasks/{task_id}", "success", user=getattr(user, "name", None))
        return _task_to_response(record)

    @app.get("/tasks/{task_id}/events")
    async def get_task_events(task_id: str) -> List[Dict[str, Any]]:
        """Get events for a task."""
        user = getattr(getattr(Request, "state", None), "user", None)
        if user and not app.state.auth.is_allowed(user, ACTION_GET_EVENTS):
            log_audit(ACTION_GET_EVENTS, f"/tasks/{task_id}/events", "denied", user=user.name)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

        store: TaskStore = app.state.store
        events = await store.get_events(task_id)
        log_audit(ACTION_GET_EVENTS, f"/tasks/{task_id}/events", "success", user=getattr(user, "name", None))
        return events

    @app.websocket("/tasks/{task_id}/ws")
    async def task_websocket(websocket: WebSocket, task_id: str):
        """WebSocket for real-time task updates."""
        await websocket.accept()
        app.state.websockets.setdefault(task_id, []).append(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                # Client can send ping/keepalive
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            app.state.websockets.get(task_id, []).remove(websocket)

    @app.get("/agents")
    async def list_agents() -> List[AgentInfo]:
        """List active agents."""
        user = getattr(getattr(Request, "state", None), "user", None)
        if user and not app.state.auth.is_allowed(user, ACTION_LIST_AGENTS):
            log_audit(ACTION_LIST_AGENTS, "/agents", "denied", user=user.name)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

        runtime: AgentRuntime = app.state.runtime
        agents = runtime.list_agents()
        log_audit(ACTION_LIST_AGENTS, "/agents", "success", user=getattr(user, "name", None))
        return [
            AgentInfo(
                name=a.config.name,
                agent_type=a.config.agent_type,
                state=a.state.value,
                model=a.config.model,
            )
            for a in agents
        ]

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Deep health check — verifies DB, worker, model registry."""
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
            version="0.2.0",
            components=components,
        )

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        """Prometheus-compatible metrics."""
        user = getattr(getattr(Request, "state", None), "user", None)
        if user and not app.state.auth.is_allowed(user, ACTION_GET_METRICS):
            log_audit(ACTION_GET_METRICS, "/metrics", "denied", user=user.name)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

        mc: MetricsCollector = app.state.metrics
        log_audit(ACTION_GET_METRICS, "/metrics", "success", user=getattr(user, "name", None))
        return mc.export()

    @app.get("/dlq")
    async def list_dlq(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """List dead letter queue entries."""
        store: TaskStore = app.state.store
        return await store.list_dlq(limit=limit, offset=offset)

    return app


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
