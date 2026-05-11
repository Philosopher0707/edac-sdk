"""FastAPI router for agent endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Request, status

from edac.agent.runtime import AgentRuntime
from edac.server.audit import log_audit
from edac.server.auth import ACTION_LIST_AGENTS
from edac.server.schemas import AgentInfo

router = APIRouter()


@router.get("/agents")
async def list_agents(request: Request) -> List[AgentInfo]:
    """List active agents."""
    app = request.app
    user = getattr(request.state, "user", None)
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
