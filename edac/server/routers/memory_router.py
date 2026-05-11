"""FastAPI router for memory inspection endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from edac.context.manager import ContextManager

router = APIRouter()


def _get_ctx(request: Request) -> ContextManager:
    ctx: Optional[ContextManager] = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Context manager not enabled",
        )
    return ctx


# ── Stats ──

@router.get("/memory/stats")
async def memory_stats(request: Request) -> Dict[str, Any]:
    """Snapshot of all memory tiers."""
    return _get_ctx(request).get_stats()


# ── Short-Term Memory ──

@router.get("/memory/agents/{agent_id}/short-term")
async def get_short_term(agent_id: str, request: Request) -> Dict[str, Any]:
    window = _get_ctx(request).get_window(agent_id)
    entries = [{"role": e.role, "content": e.content, "tokens": e.tokens} for e in window.get_window()]
    return {
        "agent_id": agent_id,
        "entries": entries,
        "total_tokens": window.total_tokens(),
    }


@router.post("/memory/agents/{agent_id}/short-term")
async def add_short_term(agent_id: str, body: Dict[str, Any], request: Request) -> Dict[str, Any]:
    role = body.get("role", "user")
    text = body["text"]
    tokens = body.get("tokens", 0)
    _get_ctx(request).add_to_window(agent_id, role, text, tokens=tokens)
    return {"agent_id": agent_id, "added": True}


@router.delete("/memory/agents/{agent_id}/short-term")
async def clear_short_term(agent_id: str, request: Request) -> Dict[str, Any]:
    _get_ctx(request).clear_window(agent_id)
    return {"agent_id": agent_id, "cleared": True}


# ── Working Memory ──

@router.get("/memory/working/{agent_id}")
async def list_working(agent_id: str, request: Request) -> List[Dict[str, Any]]:
    return _get_ctx(request).get_working(agent_id).entries


@router.get("/memory/working/{agent_id}/{key}")
async def read_working(agent_id: str, key: str, request: Request) -> Dict[str, Any]:
    value = _get_ctx(request).get_working(agent_id).read(key)
    return {"value": value}


@router.post("/memory/working/{agent_id}")
async def write_working(agent_id: str, body: Dict[str, Any], request: Request) -> Dict[str, Any]:
    wm = _get_ctx(request).get_working(agent_id)
    key = body.get("key")
    value = body.get("value")
    append = body.get("append")
    if key is not None and value is not None:
        wm.write(key, value)
    elif append is not None:
        wm.append(append)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either {'key','value'} or {'append'}",
        )
    return {"stored": True}


@router.delete("/memory/working/{agent_id}")
async def clear_working(agent_id: str, request: Request) -> Dict[str, Any]:
    _get_ctx(request).get_working(agent_id).clear()
    return {"cleared": True}


# ── Long-Term Memory ──

@router.get("/memory/long-term")
async def list_long_term(request: Request, query: Optional[str] = None) -> List[Dict[str, Any]]:
    ltm = _get_ctx(request).get_long_term()
    if query:
        results = ltm.search_text(query)
    else:
        results = list(ltm._entries.values())
    return [{"id": e.id, "content": e.content, "source": e.source, "metadata": e.metadata} for e in results]


@router.post("/memory/long-term")
async def store_long_term(body: Dict[str, Any], request: Request) -> Dict[str, Any]:
    entry_id = _get_ctx(request).store_long_term(
        content=body["content"],
        metadata=body.get("metadata"),
        source=body.get("source", "api"),
    )
    return {"stored": True, "entry_id": entry_id}


@router.delete("/memory/long-term/{entry_id}")
async def delete_long_term(entry_id: str, request: Request) -> Dict[str, Any]:
    deleted = _get_ctx(request).get_long_term().delete(entry_id)
    return {"deleted": deleted}


# ── Episodic Memory ──

@router.get("/memory/episodic/{correlation_id}")
async def get_episodic_trajectory(correlation_id: str, request: Request) -> List[Dict[str, Any]]:
    events = _get_ctx(request).get_episodic().get_trajectory(UUID(correlation_id))
    return [
        {
            "id": str(e.id),
            "type": str(e.event_type.value) if e.event_type else None,
            "source": e.source,
            "topic": getattr(e, "topic", None),
            "timestamp": str(e.timestamp) if hasattr(e, "timestamp") else None,
            "correlation_id": str(e.correlation_id) if e.correlation_id else None,
            "payload": e.payload,
        }
        for e in events
    ]


@router.get("/memory/episodic")
async def list_episodic(request: Request, event_type: Optional[str] = None) -> Dict[str, Any]:
    em = _get_ctx(request).get_episodic()
    if event_type:
        events = em.get_by_type(event_type)
    else:
        events = em._events
    return {
        "count": len(events),
        "entries": [
            {
                "id": str(e.id),
                "type": str(e.event_type.value) if e.event_type else None,
                "source": e.source,
                "topic": getattr(e, "topic", None),
                "timestamp": str(e.timestamp) if hasattr(e, "timestamp") else None,
                "correlation_id": str(e.correlation_id) if e.correlation_id else None,
            }
            for e in events
        ],
    }
