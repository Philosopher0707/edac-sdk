"""Event-driven core for the EDAC system.

The event module provides the foundational building blocks for all
communication within the agentic system. Every action, state change,
and data flow is represented as an Event flowing through the EventBus.
"""

from edac.event.schema import (
    Event,
    EventType,
    EventPriority,
    EventStatus,
    ModalityType,
    AgentState,
    PlanState,
    ToolCallState,
    HumanLoopState,
    SessionState,
    create_event,
    EventBatch,
    EventFilter,
    EventSubscription,
)
from edac.event.bus import (
    EventBus,
    EventBusConfig,
    BusStats,
    InMemoryBackend,
    EventPersistenceBackend,
)
from edac.event.router import (
    EventRouter,
    RouterConfig,
    RouteRule,
    TopicMatcher,
    RoutingStats,
)

from edac.event.vector_clock import VectorClock

__all__ = [
    # Schema
    "Event",
    "EventType",
    "EventPriority",
    "EventStatus",
    "ModalityType",
    "AgentState",
    "PlanState",
    "ToolCallState",
    "HumanLoopState",
    "SessionState",
    "create_event",
    "EventBatch",
    "EventFilter",
    "EventSubscription",
    # Bus
    "EventBus",
    "EventBusConfig",
    "BusStats",
    "InMemoryBackend",
    "EventPersistenceBackend",
    # Router
    "EventRouter",
    "RouterConfig",
    "RouteRule",
    "TopicMatcher",
    "RoutingStats",
    # Vector Clock
    "VectorClock",
]
