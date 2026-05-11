"""FastAPI HTTP API for the EDAC production server.

Routers mounted from edac.server.routers:
  tasks_router     — /tasks*, /tasks/{id}/ws (WebSocket)
  agents_router    — /agents
  system_router    — /health, /metrics, /dlq
  protocol_router  — /a2a, /events/stream, /mcp, /tools
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextManager
from edac.event.bus import EventBus
from edac.model import ModelRegistry
from edac.model.anthropic import AnthropicProvider
from edac.model.ollama import OllamaProvider
from edac.model.openai import OpenAIProvider
from edac.observability.logging import setup_logging
from edac.observability.metrics import MetricsCollector
from edac.protocol.a2a import A2ABridge
from edac.protocol.mcp_bridge import MCPBridge
from edac.protocol.sse import SSEBridge
from edac.security.guardrails import PromptGuardrail
from edac.security.secrets import SecretsManager
from edac.server.audit import log_audit
from edac.server.auth import AuthManager, Role
from edac.server.circuit_breaker import CircuitBreaker
from edac.server.config import ServerConfig
from edac.server.context import RuntimeContext
from edac.server.executor import AgentExecutor
from edac.server.rate_limiter import RateLimiter
from edac.server.routers import agents_router, approval_router, memory_router, modality_router, protocol_router, system_router, tasks_router
from edac.server.store import create_store
from edac.server.tracing import clear_request_id, get_request_id, set_request_id
from edac.observability.tracing import Tracer
from edac.server.worker import TaskWorker
from edac.tool.registry import ToolRegistry
from edac.plan.engine import PlanEngine

logger = logging.getLogger("edac.server.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown."""
    cfg: ServerConfig = app.state.config

    # Database
    store = create_store(database_url=cfg.database_url, pool_size=cfg.db_pool_size)
    await store.connect()
    app.state.store = store

    # Event bus + runtime + tracer
    tracer = Tracer()
    bus = EventBus(tracer=tracer)
    await bus.start()
    runtime = AgentRuntime(bus)
    await runtime.start()

    # Model registry
    registry = ModelRegistry()
    ollama = OllamaProvider(base_url=cfg.ollama_base_url, default_model=cfg.ollama_default_model)
    registry.register("ollama", ollama, fallback=True)

    # Approval gates (HITL)
    from edac.human.approval import ApprovalManager, ApprovalGate
    approval_manager = ApprovalManager()
    # Seed a default gate for destructive actions
    approval_manager.add_gate(
        ApprovalGate(
            trigger_on="tool.*",
            prompt="A destructive tool action requires approval.",
        )
    )

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

    # Tool registry + security
    tool_registry = ToolRegistry(bus=bus)
    tool_registry._approval_manager = approval_manager
    guardrail = PromptGuardrail()
    secrets = SecretsManager()
    secrets.load_env()
    tool_registry.secrets = secrets

    # Plan engine (orchestration)
    plan_engine = PlanEngine(bus, registry=registry)

    # Modality dispatcher
    from edac.modality.dispatcher import ModalityDispatcher
    modality_dispatcher = ModalityDispatcher()

    # Protocol bridges
    mcp_bridge = MCPBridge(tool_registry)
    a2a_bridge = A2ABridge(runtime.registry)
    sse_bridge = SSEBridge(bus)

    # Circuit breakers map for executor
    circuit_breakers: Dict[str, CircuitBreaker] = {}
    circuit_breakers["ollama"] = cb_ollama
    if anthropic:
        circuit_breakers["anthropic"] = app.state.cb_anthropic
    if openai:
        circuit_breakers["openai"] = app.state.cb_openai

    # Executor + worker
    executor = AgentExecutor(
        bus,
        runtime,
        registry,
        ctx,
        circuit_breakers=circuit_breakers,
        tools=tool_registry,
        guardrail=guardrail,
        secrets=secrets,
        plan_engine=plan_engine,
    )
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

    # Metrics collector
    metrics = MetricsCollector()

    # Auth + rate limiting
    auth = AuthManager()
    if cfg.api_key:
        auth.register(cfg.api_key, Role.ADMIN, name="default_admin")
    rate_limiter = RateLimiter(
        default_capacity=cfg.rate_limit_capacity,
        default_refill=cfg.rate_limit_refill,
    )

    # RuntimeContext — central DI container
    app.state.ctx_runtime = RuntimeContext(
        bus=bus,
        runtime=runtime,
        registry=registry,
        ctx=ctx,
        executor=executor,
        worker=worker,
        metrics=metrics,
        auth=auth,
        rate_limiter=rate_limiter,
        tool_registry=tool_registry,
        circuit_breakers=circuit_breakers,
        guardrail=guardrail,
        secrets=secrets,
        plan_engine=plan_engine,
        mcp_bridge=mcp_bridge,
        a2a_bridge=a2a_bridge,
        sse_bridge=sse_bridge,
        tracer=tracer,
        approval_manager=approval_manager,
        modality_dispatcher=modality_dispatcher,
    )

    app.state.bus = bus
    app.state.runtime = runtime
    app.state.registry = registry
    app.state.ctx = ctx
    app.state.executor = executor
    app.state.worker = worker
    app.state.metrics = metrics
    app.state.worker.metrics = metrics
    app.state.ctx.metrics = metrics
    app.state.auth = auth
    app.state.rate_limiter = rate_limiter
    app.state.tool_registry = tool_registry
    app.state.guardrail = guardrail
    app.state.secrets = secrets
    app.state.plan_engine = plan_engine
    app.state.mcp_bridge = mcp_bridge
    app.state.a2a_bridge = a2a_bridge
    app.state.sse_bridge = sse_bridge
    app.state.tracer = tracer
    app.state.approval_manager = approval_manager
    app.state.websockets: Dict[str, List[Any]] = {}

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
    setup_logging()

    app = FastAPI(
        title="EDAC",
        description="Event-Driven Agentic Core — Production API",
        version="0.3.0",
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

    # Tracing middleware — inject correlation ID + latency metrics
    @app.middleware("http")
    async def tracing_middleware(request: Request, call_next):
        rid = request.headers.get("x-request-id")
        set_request_id(rid)
        start = time.monotonic()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            elapsed = time.monotonic() - start
            mc = app.state.metrics
            mc.histogram("http_request_duration_ms", labels={"method": request.method, "route": request.url.path}).observe(elapsed * 1000)
            if response is not None:
                response.headers["x-request-id"] = get_request_id() or ""
            clear_request_id()

    # Rate limiting middleware
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        rate_limiter = app.state.rate_limiter
        client_id = request.headers.get("x-api-key", request.client.host if request.client else "anonymous")
        if not await rate_limiter.acquire(client_id):
            wait = await rate_limiter.wait_time(client_id)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": f"Rate limit exceeded. Retry after {wait:.1f}s"},
                headers={"Retry-After": str(int(wait) + 1)},
            )
        try:
            return await call_next(request)
        finally:
            await rate_limiter.release(client_id)

    # Auth middleware
    if cfg.api_key:
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            # Skip auth for health endpoint
            if request.url.path == "/health":
                return await call_next(request)

            key = request.headers.get("x-api-key")
            auth = app.state.auth
            user = auth.authenticate(key or "")
            if user is None:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid or missing API key"},
                )
            request.state.user = user
            return await call_next(request)

    # Mount routers
    app.include_router(tasks_router.router)
    app.include_router(agents_router.router)
    app.include_router(approval_router.router)
    app.include_router(memory_router.router)
    app.include_router(modality_router.router)
    app.include_router(system_router.router)
    app.include_router(protocol_router.router)

    return app
