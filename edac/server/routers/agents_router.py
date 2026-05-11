"""FastAPI router for agent endpoints.

Provides full CRUD + lifecycle actions for agents managed by the
:class:`~edac.agent.runtime.AgentRuntime`.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request, status

from edac.agent.lifecycle import AgentConfig
from edac.agent.runtime import AgentRuntime
from edac.server.audit import log_audit
from edac.server.auth import (
    ACTION_CREATE_AGENT,
    ACTION_DELETE_AGENT,
    ACTION_GET_AGENT,
    ACTION_LIST_AGENTS,
    ACTION_PAUSE_AGENT,
    ACTION_RESTART_AGENT,
    ACTION_RESUME_AGENT,
)
from edac.server.schemas import AgentInfo, BatchError, CreateAgentRequest

router = APIRouter()


# ── Helpers ──


def _to_info(agent) -> AgentInfo:
    """Build an *AgentInfo* from a live :class:`~edac.agent.lifecycle.AgentInstance`."""
    return AgentInfo(
        agent_id=agent.agent_id,
        name=agent.config.name,
        agent_type=agent.config.agent_type,
        state=agent.state.value,
        model=agent.config.model,
    )


def _paginated_response(items: List[Any], total: int, limit: int, offset: int) -> Dict[str, Any]:
    """Build a paginated response dict with metadata headers."""
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── Endpoints ──


@router.get("/agents")
async def list_agents(
    request: Request,
    limit: int = 100,
    offset: int = 0,
) -> List[AgentInfo]:
    """List all active agents (paginated)."""
    app = request.app
    user = getattr(request.state, "user", None)

    if user and not app.state.auth.is_allowed(user, ACTION_LIST_AGENTS):
        log_audit(ACTION_LIST_AGENTS, "/agents", "denied", user=user.name)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    runtime: AgentRuntime = app.state.runtime
    all_agents = runtime.list_agents()
    total = len(all_agents)
    paginated = all_agents[offset : offset + limit]

    log_audit(
        ACTION_LIST_AGENTS,
        "/agents",
        "success",
        user=getattr(user, "name", None),
    )
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content=[_to_info(a).model_dump() for a in paginated],
        headers={
            "X-Total-Count": str(total),
            "X-Limit": str(limit),
            "X-Offset": str(offset),
        },
    )


@router.post("/agents", response_model=AgentInfo, status_code=status.HTTP_201_CREATED)
async def create_agent(
    req: CreateAgentRequest,
    request: Request,
) -> AgentInfo:
    """Create and start a new agent."""
    app = request.app
    user = getattr(request.state, "user", None)

    if user and not app.state.auth.is_allowed(user, ACTION_CREATE_AGENT):
        log_audit(ACTION_CREATE_AGENT, "/agents", "denied", user=user.name)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    runtime: AgentRuntime = app.state.runtime
    config = AgentConfig(
        name=req.name,
        agent_type=req.agent_type,
        model=req.model,
        skills=req.skills or [],
        goal=req.goal,
        sandbox=req.sandbox,
        max_restarts=req.max_restarts,
    )

    try:
        agent = await runtime.spawn(config)
    except Exception as exc:
        log_audit(
            ACTION_CREATE_AGENT,
            "/agents",
            "error",
            user=getattr(user, "name", None),
            details={"error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to spawn agent: {exc}",
        )

    log_audit(
        ACTION_CREATE_AGENT,
        f"/agents/{agent.agent_id}",
        "success",
        user=getattr(user, "name", None),
    )
    return _to_info(agent)


@router.post("/agents/batch")
async def create_agents_batch(
    reqs: List[CreateAgentRequest],
    request: Request,
) -> List[Dict[str, Any]]:
    """Create multiple agents in a single request.

    Returns a list where each element is either an *AgentInfo* dict or a
    *BatchError* dict (containing ``error`` and ``detail`` keys).
    """
    app = request.app
    user = getattr(request.state, "user", None)
    runtime: AgentRuntime = app.state.runtime
    results: List[Dict[str, Any]] = []

    for req in reqs:
        if user and not app.state.auth.is_allowed(user, ACTION_CREATE_AGENT):
            results.append({"error": "Permission denied", "detail": ACTION_CREATE_AGENT})
            continue
        config = AgentConfig(
            name=req.name,
            agent_type=req.agent_type,
            model=req.model,
            skills=req.skills or [],
            goal=req.goal,
            sandbox=req.sandbox,
            max_restarts=req.max_restarts,
        )
        try:
            agent = await runtime.spawn(config)
            results.append(_to_info(agent).model_dump())
        except Exception as exc:
            results.append({"error": "Failed to spawn agent", "detail": str(exc)})

    return results


@router.delete("/agents/batch")
async def delete_agents_batch(
    payload: Dict[str, Any],
    request: Request,
) -> List[Dict[str, str]]:
    """Delete multiple agents in a single request.

    Request body: ``{"agent_ids": ["id1", "id2", ...]}``
    """
    app = request.app
    user = getattr(request.state, "user", None)
    runtime: AgentRuntime = app.state.runtime
    agent_ids = payload.get("agent_ids", [])
    results: List[Dict[str, str]] = []

    for agent_id in agent_ids:
        if user and not app.state.auth.is_allowed(user, ACTION_DELETE_AGENT):
            results.append({"agent_id": agent_id, "status": "denied"})
            continue
        agent = runtime.get_agent(agent_id)
        if agent is None:
            results.append({"agent_id": agent_id, "status": "not_found"})
            continue
        await runtime.kill(agent_id, reason="batch_delete")
        log_audit(
            ACTION_DELETE_AGENT,
            f"/agents/{agent_id}",
            "success",
            user=getattr(user, "name", None),
        )
        results.append({"agent_id": agent_id, "status": "deleted"})

    return results


@router.get("/agents/{agent_id}", response_model=AgentInfo)
async def get_agent(agent_id: str, request: Request) -> AgentInfo:
    """Get information about a single agent."""
    app = request.app
    user = getattr(request.state, "user", None)

    if user and not app.state.auth.is_allowed(user, ACTION_GET_AGENT):
        log_audit(ACTION_GET_AGENT, f"/agents/{agent_id}", "denied", user=user.name)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    runtime: AgentRuntime = app.state.runtime
    agent = runtime.get_agent(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )
    log_audit(
        ACTION_GET_AGENT,
        f"/agents/{agent_id}",
        "success",
        user=getattr(user, "name", None),
    )
    return _to_info(agent)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_200_OK)
async def delete_agent(agent_id: str, request: Request) -> Dict[str, str]:
    """Terminate and remove an agent."""
    app = request.app
    user = getattr(request.state, "user", None)

    if user and not app.state.auth.is_allowed(user, ACTION_DELETE_AGENT):
        log_audit(ACTION_DELETE_AGENT, f"/agents/{agent_id}", "denied", user=user.name)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    runtime: AgentRuntime = app.state.runtime
    agent = runtime.get_agent(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )

    await runtime.kill(agent_id, reason="api_delete")
    log_audit(
        ACTION_DELETE_AGENT,
        f"/agents/{agent_id}",
        "success",
        user=getattr(user, "name", None),
    )
    return {"status": "deleted", "agent_id": agent_id}


@router.post("/agents/{agent_id}/restart", response_model=AgentInfo)
async def restart_agent(agent_id: str, request: Request) -> AgentInfo:
    """Restart an agent, preserving its configuration."""
    app = request.app
    user = getattr(request.state, "user", None)

    if user and not app.state.auth.is_allowed(user, ACTION_RESTART_AGENT):
        log_audit(
            ACTION_RESTART_AGENT, f"/agents/{agent_id}", "denied", user=user.name
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    runtime: AgentRuntime = app.state.runtime
    agent = runtime.get_agent(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )

    new_agent = await runtime.restart(agent_id)
    if new_agent is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to restart agent '{agent_id}'",
        )

    log_audit(
        ACTION_RESTART_AGENT,
        f"/agents/{new_agent.agent_id}",
        "success",
        user=getattr(user, "name", None),
    )
    return _to_info(new_agent)


@router.post("/agents/{agent_id}/pause", response_model=AgentInfo)
async def pause_agent(agent_id: str, request: Request) -> AgentInfo:
    """Pause a running agent."""
    app = request.app
    user = getattr(request.state, "user", None)

    if user and not app.state.auth.is_allowed(user, ACTION_PAUSE_AGENT):
        log_audit(ACTION_PAUSE_AGENT, f"/agents/{agent_id}", "denied", user=user.name)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    runtime: AgentRuntime = app.state.runtime
    agent = runtime.get_agent(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )

    from edac.event.schema import AgentState

    await agent.transition(AgentState.PAUSED)
    log_audit(
        ACTION_PAUSE_AGENT,
        f"/agents/{agent_id}",
        "success",
        user=getattr(user, "name", None),
    )
    return _to_info(agent)


@router.post("/agents/{agent_id}/resume", response_model=AgentInfo)
async def resume_agent(agent_id: str, request: Request) -> AgentInfo:
    """Resume a paused agent."""
    app = request.app
    user = getattr(request.state, "user", None)

    if user and not app.state.auth.is_allowed(user, ACTION_RESUME_AGENT):
        log_audit(
            ACTION_RESUME_AGENT, f"/agents/{agent_id}", "denied", user=user.name
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    runtime: AgentRuntime = app.state.runtime
    agent = runtime.get_agent(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )

    from edac.event.schema import AgentState

    await agent.transition(AgentState.IDLE)
    log_audit(
        ACTION_RESUME_AGENT,
        f"/agents/{agent_id}",
        "success",
        user=getattr(user, "name", None),
    )
    return _to_info(agent)
