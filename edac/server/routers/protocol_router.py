"""FastAPI router for protocol endpoints (A2A, SSE, MCP, tools)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from edac.protocol.a2a import A2ABridge, A2AMessage
from edac.protocol.mcp_bridge import MCPBridge
from edac.protocol.sse import SSEBridge
from edac.tool.registry import ToolRegistry

router = APIRouter()


@router.get("/a2a/agents")
async def a2a_discover(request: Request):
    """A2A protocol: discover available agent cards."""
    app = request.app
    bridge: Optional[A2ABridge] = getattr(app.state, "a2a_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="A2A bridge not enabled")
    return PlainTextResponse(content=bridge.discover(), media_type="application/json")


@router.get("/a2a/agents/{name}/card")
async def a2a_agent_card(name: str, request: Request):
    """A2A protocol: get a single agent card by name."""
    app = request.app
    bridge: Optional[A2ABridge] = getattr(app.state, "a2a_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="A2A bridge not enabled")
    card = bridge.get_card(name)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found")
    return JSONResponse(content=card)


@router.post("/a2a/tasks")
async def a2a_create_task(request: Request) -> JSONResponse:
    """A2A protocol: create a new task."""
    app = request.app
    bridge: Optional[A2ABridge] = getattr(app.state, "a2a_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="A2A bridge not enabled")
    try:
        body = await request.json()
    except Exception:
        body = {}
    task_id = body.get("id", f"task-{id(body)}")
    message = None
    if "message" in body:
        msg = body["message"]
        message = A2AMessage(
            role=msg.get("role", "user"),
            parts=msg.get("parts", []),
            metadata=msg.get("metadata", {}),
        )
    task = bridge.create_task(task_id, message=message)
    bus: Optional[Any] = getattr(app.state, "bus", None)
    if bus is not None:
        from edac.event.schema import EventType, EventPriority, create_event
        event = create_event(
            event_type=EventType.PLAN_CREATE,
            source="protocol:a2a",
            topic="a2a.tasks",
            payload={"task_id": task.id, "status": task.status.value},
            priority=EventPriority.NORMAL,
        )
        await bus.emit(event)
    return JSONResponse(content=task.to_dict())


@router.get("/events/stream")
async def sse_stream(request: Request, topics: Optional[str] = None):
    """SSE stream for real-time events.

    Query `topics` is a comma-separated list of topic patterns.
    """
    app = request.app
    bridge: Optional[SSEBridge] = getattr(app.state, "sse_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="SSE bridge not enabled")

    topic_list: Optional[List[str]] = topics.split(",") if topics else None

    async def event_generator():
        async for chunk in bridge.stream(topics=topic_list):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/mcp/tools")
async def mcp_list_tools(request: Request) -> PlainTextResponse:
    """MCP protocol: list available tools."""
    app = request.app
    bridge: Optional[MCPBridge] = getattr(app.state, "mcp_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="MCP bridge not enabled")
    return PlainTextResponse(content=bridge.list_tools(), media_type="application/json")


@router.post("/mcp/tools/{tool_name}")
async def mcp_call_tool(tool_name: str, request: Request) -> PlainTextResponse:
    """MCP protocol: call a tool by name."""
    app = request.app
    bridge: Optional[MCPBridge] = getattr(app.state, "mcp_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="MCP bridge not enabled")
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = await bridge.call_tool(tool_name, body)
    return PlainTextResponse(content=result, media_type="application/json")


@router.get("/tools")
async def list_tools(request: Request) -> List[Dict[str, Any]]:
    """List all registered tools."""
    app = request.app
    tool_registry: Optional[ToolRegistry] = getattr(app.state, "tool_registry", None)
    if tool_registry is None:
        return []
    tools = tool_registry.list_tools()
    return [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools]
