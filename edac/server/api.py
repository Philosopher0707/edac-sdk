"""FastAPI HTTP API for the EDAC production server.

Endpoints:
  POST /tasks              — submit a new task
  GET  /tasks              — list tasks
  GET  /tasks/{id}         — get task by id
  GET  /tasks/{id}/events  — get task events
  GET  /agents             — list agents
  GET  /health             — health check
  GET  /metrics            — Prometheus metrics
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextManager
from edac.event.bus import EventBus
from edac.model import ModelRegistry
from edac.model.anthropic import AnthropicProvider
from edac.model.ollama import OllamaProvider
from edac.model.openai import OpenAIProvider
from edac.observability.metrics import MetricsCollector
from edac.server.config import ServerConfig
from edac.server.executor import AgentExecutor
from edac.server.store import TaskStore
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

    if cfg.anthropic_api_key:
        anthropic = AnthropicProvider(
            api_key=cfg.anthropic_api_key,
            default_model=cfg.anthropic_default_model,
        )
        registry.register("anthropic", anthropic)

    if cfg.openai_api_key:
        openai = OpenAIProvider(
            api_key=cfg.openai_api_key,
            default_model=cfg.openai_default_model,
            base_url=cfg.openai_base_url,
        )
        registry.register("openai", openai)

    # Context manager
    ctx = ContextManager(registry=registry)

    # Executor + worker
    executor = AgentExecutor(bus, runtime, registry, ctx)
    worker = TaskWorker(
        store=store,
        executor=executor,
        maxsize=cfg.task_queue_maxsize,
    )
    await worker.start()

    app.state.bus = bus
    app.state.runtime = runtime
    app.state.registry = registry
    app.state.ctx = ctx
    app.state.executor = executor
    app.state.worker = worker
    app.state.metrics = MetricsCollector()

    logger.info("EDAC server started")
    yield

    # Shutdown
    logger.info("EDAC server shutting down...")
    await worker.stop()
    await runtime.stop()
    await bus.stop()
    await store.close()
    await ollama.close()
    if cfg.anthropic_api_key:
        await anthropic.close()
    if cfg.openai_api_key:
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

    # API Key middleware
    if cfg.api_key:
        @app.middleware("http")
        async def api_key_middleware(request: Request, call_next):
            key = request.headers.get("x-api-key")
            if key != cfg.api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key"},
                )
            return await call_next(request)

    # ── Routes ──

    @app.post("/tasks", response_model=TaskResponse)
    async def submit_task(req: SubmitTaskRequest) -> TaskResponse:
        """Submit a new multi-agent task."""
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

        record = await store.get_task(task_id)
        return _task_to_response(record)

    @app.get("/tasks")
    async def list_tasks(
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TaskResponse]:
        """List tasks."""
        store: TaskStore = app.state.store
        records = await store.list_tasks(status=status, limit=limit, offset=offset)
        return [_task_to_response(r) for r in records]

    @app.get("/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str) -> TaskResponse:
        """Get a task by id."""
        store: TaskStore = app.state.store
        record = await store.get_task(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return _task_to_response(record)

    @app.get("/tasks/{task_id}/events")
    async def get_task_events(task_id: str) -> List[Dict[str, Any]]:
        """Get events for a task."""
        store: TaskStore = app.state.store
        events = await store.get_events(task_id)
        return events

    @app.get("/agents")
    async def list_agents() -> List[AgentInfo]:
        """List active agents."""
        runtime: AgentRuntime = app.state.runtime
        agents = runtime.list_agents()
        return [
            AgentInfo(
                name=a.config.name,
                agent_type=a.config.agent_type,
                state=a.state.value,
                model=a.config.model,
            )
            for a in agents
        ]

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        """Health check."""
        return {
            "status": "healthy",
            "version": "0.2.0",
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        """Prometheus-compatible metrics."""
        mc: MetricsCollector = app.state.metrics
        return mc.export()

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
