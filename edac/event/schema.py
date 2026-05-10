"""
Universal Event Schema for EDAC.

Every action, state change, communication, and observation in the system
is represented as an Event. This enables:
- Complete observability (everything is logged)
- Replay capability (reconstruct any session from events)
- Distributed tracing (causality chains across agents)
- Modality-agnostic processing (text, code, image, audio, video all use same schema)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum, auto
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Set,
    Union,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ──────────────────────────────────────────────────────────────
# Core State Enums (referenced by __init__)
# ──────────────────────────────────────────────────────────────

class EventStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class ModalityType(str, Enum):
    TEXT = "text"
    CODE = "code"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    ARTIFACT = "artifact"
    EMBEDDING = "embedding"


class AgentState(str, Enum):
    INITIALIZING = "initializing"
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    ERROR = "error"
    TERMINATED = "terminated"


class PlanState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    REPLANNING = "replanning"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class ToolCallState(str, Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class HumanLoopState(str, Enum):
    SUBMITTED = "submitted"
    WAITING = "waiting"
    RESPONDED = "responded"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class SessionState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"
    ERROR = "error"


# ──────────────────────────────────────────────────────────────
# Event Type Taxonomy
# ──────────────────────────────────────────────────────────────

class EventCategory(str, Enum):
    """High-level event categories."""
    AGENT = "agent"
    PLAN = "plan"
    TOOL = "tool"
    HUMAN = "human"
    MODALITY = "modality"
    SYSTEM = "system"
    PROTOCOL = "protocol"


class EventType(str, Enum):
    """All event types in the system.
    
    Naming convention: {category}.{action}[.{subaction}]
    """
    # Agent lifecycle
    AGENT_SPAWN = "agent.spawn"
    AGENT_HEARTBEAT = "agent.heartbeat"
    AGENT_PAUSE = "agent.pause"
    AGENT_RESUME = "agent.resume"
    AGENT_TERMINATE = "agent.terminate"
    AGENT_CRASH = "agent.crash"
    AGENT_STATE_CHANGE = "agent.state_change"
    
    # Plan lifecycle
    PLAN_CREATE = "plan.create"
    PLAN_UPDATE = "plan.update"
    PLAN_REPLAN = "plan.replan"
    PLAN_COMPLETE = "plan.complete"
    PLAN_ABORT = "plan.abort"
    PLAN_STEP_START = "plan.step.start"
    PLAN_STEP_COMPLETE = "plan.step.complete"
    PLAN_STEP_FAIL = "plan.step.fail"
    PLAN_STEP_SKIP = "plan.step.skip"
    PLAN_STEP_RETRY = "plan.step.retry"
    
    # Tool interactions
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    TOOL_TIMEOUT = "tool.timeout"
    TOOL_DISCOVER = "tool.discover"
    TOOL_REGISTER = "tool.register"
    
    # Human interactions
    HUMAN_INPUT_REQUIRED = "human.input_required"
    HUMAN_APPROVAL = "human.approval"
    HUMAN_FEEDBACK = "human.feedback"
    HUMAN_OVERRIDE = "human.override"
    HUMAN_MESSAGE = "human.message"
    
    # Modality content
    MODALITY_TEXT = "modality.text"
    MODALITY_CODE = "modality.code"
    MODALITY_IMAGE = "modality.image"
    MODALITY_AUDIO = "modality.audio"
    MODALITY_VIDEO = "modality.video"
    MODALITY_ARTIFACT = "modality.artifact"
    MODALITY_EMBEDDING = "modality.embedding"
    
    # System events
    SYSTEM_ERROR = "system.error"
    SYSTEM_METRIC = "system.metric"
    SYSTEM_LOG = "system.log"
    SYSTEM_CONFIG_CHANGE = "system.config_change"
    SYSTEM_SHUTDOWN = "system.shutdown"
    
    # Protocol events
    PROTOCOL_A2A_TASK = "protocol.a2a.task"
    PROTOCOL_A2A_ARTIFACT = "protocol.a2a.artifact"
    PROTOCOL_MCP_CALL = "protocol.mcp.call"
    PROTOCOL_MCP_RESULT = "protocol.mcp.result"
    PROTOCOL_SSE_CONNECT = "protocol.sse.connect"
    PROTOCOL_SSE_DISCONNECT = "protocol.sse.disconnect"


# ──────────────────────────────────────────────────────────────
# Priority System
# ──────────────────────────────────────────────────────────────

class EventPriority(int, Enum):
    """Event priority levels.
    
    Lower numbers = higher priority (processed first).
    """
    CRITICAL = -100   # System failure, agent crash
    HIGH = -50        # Human waiting, deadline approaching
    NORMAL = 0        # Standard processing
    LOW = 50          # Background tasks, cleanup
    BATCH = 100       # Bulk operations, analytics


# ──────────────────────────────────────────────────────────────
# Core Event Model
# ──────────────────────────────────────────────────────────────

class Event(BaseModel):
    """The universal event — everything in EDAC is an Event.
    
    Events are immutable once created. To "modify" an event,
    emit a new event with the original as parent.
    """
    
    model_config = ConfigDict(
        frozen=True,  # Immutable events
        extra="allow",  # Allow extension for custom fields
        json_schema_extra={
            "examples": [
                {
                    "event_id": "550e8400-e29b-41d4-a716-446655440000",
                    "event_type": "agent.spawn",
                    "correlation_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "source": "system:orchestrator",
                    "topic": "agent.swarm.coding",
                    "payload": {"agent_name": "code-reviewer", "goal": "Refactor auth module"},
                    "timestamp": "2025-01-15T10:30:00Z",
                    "priority": 0,
                }
            ]
        },
    )
    
    # ── Identity ──
    event_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Unique identifier for this event",
    )
    event_type: EventType = Field(
        description="The type of event (determines payload schema)",
    )
    
    # ── Causality (Distributed Tracing) ──
    correlation_id: uuid.UUID = Field(
        description="Groups all events in a single task/session",
    )
    parent_event_id: Optional[uuid.UUID] = Field(
        default=None,
        description="The event that directly caused this event",
    )
    causality_vector: Dict[str, int] = Field(
        default_factory=dict,
        description="Vector clock for distributed event ordering. "
                    "Keys are agent IDs, values are logical timestamps.",
    )
    
    # ── Source ──
    source: str = Field(
        description="Event emitter identifier. Format: '{type}:{name}'",
        examples=["agent:planner-1", "tool:github-mcp", "human:alice", "system:monitor"],
    )
    
    # ── Payload (Modality-Agnostic) ──
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Event-type-specific data. Schema varies by event_type.",
    )
    
    # ── Metadata ──
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when event was created",
    )
    priority: EventPriority = Field(
        default=EventPriority.NORMAL,
        description="Processing priority",
    )
    ttl_seconds: Optional[float] = Field(
        default=None,
        description="Time-to-live. Event can be dropped after this many seconds.",
    )
    
    # ── Routing ──
    topic: str = Field(
        description="Pub/sub topic for routing. Hierarchical: 'category.subcategory.name'",
        examples=["agent.swarm.coding", "plan.auth-module", "human.approval-queue"],
    )
    target_agents: Optional[Set[str]] = Field(
        default=None,
        description="Specific recipient agent IDs. None = broadcast to topic subscribers.",
    )
    
    # ── Context Snapshot ──
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of relevant state at event creation time. "
                    "Includes token usage, agent state, plan progress, etc.",
    )
    
    # ── Validation ──
    @field_validator("source")
    @classmethod
    def validate_source_format(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError("Source must be in format 'type:name' (e.g., 'agent:planner-1')")
        return v
    
    # ── Methods ──
    def get_category(self) -> EventCategory:
        """Extract category from event type."""
        category_str = self.event_type.value.split(".")[0]
        return EventCategory(category_str)
    
    def is_agent_event(self) -> bool:
        return self.get_category() == EventCategory.AGENT
    
    def is_plan_event(self) -> bool:
        return self.get_category() == EventCategory.PLAN
    
    def is_human_event(self) -> bool:
        return self.get_category() == EventCategory.HUMAN
    
    def is_system_critical(self) -> bool:
        return self.priority == EventPriority.CRITICAL
    
    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Check if event has exceeded its TTL."""
        if self.ttl_seconds is None:
            return False
        now = now or datetime.now(timezone.utc)
        elapsed = (now - self.timestamp).total_seconds()
        return elapsed > self.ttl_seconds
    
    def derive(
        self,
        event_type: EventType,
        payload: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Event:
        """Create a child event with this event as parent.
        
        Preserves correlation_id, increments causality vector.
        """
        # Increment causality vector for source
        source_type, source_name = self.source.split(":", 1)
        new_vector = dict(self.causality_vector)
        new_vector[source_name] = new_vector.get(source_name, 0) + 1
        
        return Event(
            event_type=event_type,
            correlation_id=self.correlation_id,
            parent_event_id=self.event_id,
            causality_vector=new_vector,
            source=kwargs.get("source", self.source),
            payload=payload or {},
            topic=kwargs.get("topic", self.topic),
            priority=kwargs.get("priority", self.priority),
            **{k: v for k, v in kwargs.items() if k not in {"source", "topic", "priority"}},
        )
    
    def __repr__(self) -> str:
        return (
            f"Event({self.event_type.value} "
            f"id={self.event_id.hex[:8]} "
            f"src={self.source} "
            f"topic={self.topic})"
        )


# ──────────────────────────────────────────────────────────────
# Typed Payload Schemas (for common event types)
# ──────────────────────────────────────────────────────────────

class AgentSpawnPayload(BaseModel):
    """Payload for agent.spawn events."""
    agent_name: str
    agent_type: str
    goal: Optional[str] = None
    plan_id: Optional[uuid.UUID] = None
    parent_agent_id: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)


class PlanStepPayload(BaseModel):
    """Payload for plan step events."""
    plan_id: uuid.UUID
    step_id: str
    step_index: int
    step_type: str
    description: str
    dependencies: List[str] = Field(default_factory=list)
    input_artifacts: List[str] = Field(default_factory=list)
    output_artifacts: List[str] = Field(default_factory=list)


class ToolCallPayload(BaseModel):
    """Payload for tool.call events."""
    tool_name: str
    tool_type: Literal["mcp", "skill", "builtin", "sandbox"]
    arguments: Dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = 30.0
    sandbox_config: Optional[Dict[str, Any]] = None


class ToolResultPayload(BaseModel):
    """Payload for tool.result events."""
    tool_name: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    duration_ms: float
    tokens_used: Optional[int] = None


class HumanInputPayload(BaseModel):
    """Payload for human.input_required events."""
    prompt: str
    input_type: Literal["text", "approval", "choice", "file"]
    options: Optional[List[str]] = None
    timeout_seconds: Optional[float] = None
    blocking: bool = True


class ModalityPayload(BaseModel):
    """Base for all modality events."""
    content_type: str  # MIME type
    encoding: Literal["raw", "base64", "url", "embedding"]
    data: Union[str, bytes, List[float]]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    size_bytes: int


class SystemMetricPayload(BaseModel):
    """Payload for system.metric events."""
    metric_name: str
    metric_type: Literal["counter", "gauge", "histogram", "summary"]
    value: float
    labels: Dict[str, str] = Field(default_factory=dict)
    unit: Optional[str] = None


# ──────────────────────────────────────────────────────────────
# Event Filters (for subscriptions)
# ──────────────────────────────────────────────────────────────

class EventFilter(BaseModel):
    """Filter criteria for event subscriptions."""
    
    model_config = ConfigDict(extra="allow")
    
    event_types: Optional[Set[EventType]] = None
    categories: Optional[Set[EventCategory]] = None
    sources: Optional[Set[str]] = None
    topics: Optional[Set[str]] = None
    min_priority: Optional[EventPriority] = None
    max_priority: Optional[EventPriority] = None
    correlation_ids: Optional[Set[uuid.UUID]] = None
    
    def matches(self, event: Event) -> bool:
        """Check if an event matches this filter."""
        if self.event_types and event.event_type not in self.event_types:
            return False
        if self.categories and event.get_category() not in self.categories:
            return False
        if self.sources and event.source not in self.sources:
            return False
        if self.topics:
            # Support wildcard matching: "agent.swarm.*" matches "agent.swarm.coding"
            matched = False
            for topic_pattern in self.topics:
                if self._topic_matches(event.topic, topic_pattern):
                    matched = True
                    break
            if not matched:
                return False
        if self.min_priority and event.priority.value > self.min_priority.value:
            return False
        if self.max_priority and event.priority.value < self.max_priority.value:
            return False
        if self.correlation_ids and event.correlation_id not in self.correlation_ids:
            return False
        return True
    
    @staticmethod
    def _topic_matches(topic: str, pattern: str) -> bool:
        """Support hierarchical wildcard matching."""
        if pattern == "*" or pattern == "#":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return topic.startswith(prefix + ".")
        if pattern.endswith(".#"):
            prefix = pattern[:-2]
            return topic.startswith(prefix)
        return topic == pattern


# ──────────────────────────────────────────────────────────────
# Convenience Functions
# ──────────────────────────────────────────────────────────────

def create_event(
    event_type: EventType,
    source: str,
    topic: str,
    correlation_id: Optional[uuid.UUID] = None,
    payload: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Event:
    """Convenience factory for creating events.

    correlation_id defaults to a new UUID if not provided.
    """
    return Event(
        event_type=event_type,
        source=source,
        topic=topic,
        correlation_id=correlation_id or uuid.uuid4(),
        payload=payload or {},
        **kwargs,
    )


def create_system_event(
    event_type: EventType,
    payload: Dict[str, Any],
    priority: EventPriority = EventPriority.NORMAL,
) -> Event:
    """Convenience factory for system events."""
    return create_event(
        event_type=event_type,
        source="system:core",
        topic="system.events",
        payload=payload,
        priority=priority,
    )


# ──────────────────────────────────────────────────────────────
# Batch / Subscription helpers
# ──────────────────────────────────────────────────────────────

class EventBatch(BaseModel):
    """A batch of events for efficient transport."""
    model_config = ConfigDict(frozen=True)
    events: List[Event] = Field(default_factory=list)
    correlation_id: Optional[uuid.UUID] = None


class EventSubscription(BaseModel):
    """A subscription descriptor."""
    model_config = ConfigDict(frozen=True)
    id: str
    topic_pattern: str
    event_types: Optional[Set[EventType]] = None
    priority_min: Optional[EventPriority] = None
