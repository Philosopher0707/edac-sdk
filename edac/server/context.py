"""RuntimeContext — Dependency-injection backbone for EDAC production server.

Centralizes all runtime components so downstream code receives a single
injected object instead of positional args scattered across constructors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextManager
from edac.event.bus import EventBus
from edac.model import ModelRegistry
from edac.observability.metrics import MetricsCollector
from edac.server.auth import AuthManager
from edac.server.circuit_breaker import CircuitBreaker
from edac.server.executor import AgentExecutor
from edac.server.rate_limiter import RateLimiter
from edac.server.worker import TaskWorker
from edac.tool.registry import ToolRegistry


@dataclass
class RuntimeContext:
    """Central dependency-injection container for the EDAC runtime.

    All fields have defaults (None or empty) so new code paths can be added
    without breaking existing callers that construct a partial context.
    """

    # Core (always present)
    bus: EventBus
    runtime: AgentRuntime
    registry: ModelRegistry  # LLM model registry
    ctx: ContextManager
    executor: AgentExecutor
    worker: TaskWorker
    metrics: MetricsCollector
    auth: AuthManager
    rate_limiter: RateLimiter

    # Phase 1 — Foundation
    tool_registry: Optional[ToolRegistry] = None
    circuit_breakers: Dict[str, CircuitBreaker] = field(default_factory=dict)

    # Phase 1.2 — Security
    guardrail: Optional[Any] = None
    secrets: Optional[Any] = None

    # Phase 2.1 — Orchestration
    plan_engine: Optional[Any] = None

    # Phase 3.1 — HITL
    approval_manager: Optional[Any] = None

    # Phase 3.2 — Protocol bridges
    mcp_bridge: Optional[Any] = None
    a2a_bridge: Optional[Any] = None
    sse_bridge: Optional[Any] = None

    # Phase 4.2 — Observability
    tracer: Optional[Any] = None

    # Phase 4.3 — Modality
    modality_dispatcher: Optional[Any] = None

    # Phase 5 — Chat sessions
    chat_store: Optional[Any] = None
