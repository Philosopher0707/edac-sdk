"""Pydantic schemas for the EDAC production server API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
