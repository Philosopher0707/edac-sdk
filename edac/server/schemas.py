"""Pydantic schemas for the EDAC production server API."""

from __future__ import annotations

from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class SubmitTaskRequest(BaseModel):
    """Request to submit a new task."""

    goal: str = Field(..., min_length=1, max_length=10000)
    pattern: str = Field(default="pipeline", pattern="^(pipeline|mesh|orchestrator-workers)$")
    agents: List[Dict[str, Any]] = Field(default_factory=list)
    max_parallel: int = Field(default=3, ge=1, le=20)
    webhook_url: Optional[str] = Field(default=None, description="URL to POST task result when completed/failed")


class WebhookConfig(BaseModel):
    """Configuration for a webhook endpoint."""

    url: str
    events: List[str] = Field(default_factory=lambda: ["task.completed", "task.failed"])


class WebhookDelivery(BaseModel):
    """Record of a webhook delivery attempt."""

    url: str
    task_id: str
    status: str
    attempt: int
    response_status: Optional[int] = None
    delivered_at: Optional[float] = None
    error: Optional[str] = None


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


class CreateAgentRequest(BaseModel):
    """Request to create a new agent."""

    name: str = Field(..., min_length=1, max_length=256)
    agent_type: str = Field(default="generic")
    model: Optional[str] = None
    skills: Optional[List[str]] = Field(default_factory=list)
    goal: Optional[str] = None
    sandbox: bool = False
    max_restarts: int = Field(default=3, ge=0)


class AgentInfo(BaseModel):
    """Agent information."""

    agent_id: str
    name: str
    agent_type: str
    state: str
    model: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    components: Dict[str, Any]


class BatchError(BaseModel):
    """Error entry within a batch response."""

    error: str
    detail: Optional[str] = None
    index: Optional[int] = None


class PaginatedList(BaseModel, Generic[T]):
    """Generic paginated list response with total count."""

    items: List[T]
    total: int
    limit: int
    offset: int


class TaskUpdate(BaseModel):
    """Real-time task update pushed over WebSocket or SSE."""

    type: str
    task_id: str
    status: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    updated_at: Optional[str] = None
    event_type: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None
