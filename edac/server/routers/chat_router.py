"""Chat API router — REST + WebSocket endpoints for conversational sessions.

POST   /chat/sessions            — Create a new chat session
POST   /chat/sessions/{id}/send  — Send a message (non-streaming)
POST   /chat/sessions/{id}/stream — SSE streaming response
WS     /chat/sessions/{id}/ws    — Bidirectional WebSocket chat
GET    /chat/sessions/{id}       — Get session history
DELETE /chat/sessions/{id}       — End a session
GET    /chat/sessions            — List sessions
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from edac.chat.store import ChatStore
from edac.event.schema import Event, EventType, create_event, EventPriority
from edac.context.manager import ContextManager
from edac.model import ModelRegistry, ChatMessage
from edac.server.audit import log_audit
from edac.server.auth import ACTION_CHAT_CREATE, ACTION_CHAT_DELETE, ACTION_CHAT_GET, ACTION_CHAT_SEND

logger = logging.getLogger("edac.server.routers.chat")

router = APIRouter(prefix="/chat", tags=["chat"])


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    """Request body for creating a chat session."""
    model: str = Field(default="llama3.2", description="Model to use")
    provider: str = Field(default="ollama", description="Model provider")
    system_prompt: Optional[str] = Field(default=None, description="System prompt")
    title: Optional[str] = Field(default=None, description="Optional session title")


class SendMessageRequest(BaseModel):
    """Request body for sending a chat message."""
    message: str = Field(..., description="User message text")
    stream: bool = Field(default=False, description="Whether to stream the response")


class ChatMessageResponse(BaseModel):
    """A single message in the response."""
    role: str
    content: str
    timestamp: str
    message_id: str


class ChatSessionResponse(BaseModel):
    """Chat session metadata."""
    session_id: str
    title: Optional[str]
    model: str
    provider: str
    created_at: str
    updated_at: str
    message_count: int


class ChatHistoryResponse(BaseModel):
    """Full session history."""
    session_id: str
    title: Optional[str]
    messages: List[ChatMessageResponse]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_store(request: Request) -> ChatStore:
    """Get ChatStore from app state (created in lifespan)."""
    store: ChatStore = getattr(request.app.state, "chat_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat store not initialized",
        )
    return store


def _get_ctx(request: Request) -> ContextManager:
    """Get ContextManager from app state."""
    ctx: ContextManager = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Context manager not initialized",
        )
    return ctx


def _get_registry(request: Request) -> ModelRegistry:
    """Get ModelRegistry from app state."""
    registry: ModelRegistry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model registry not initialized",
        )
    return registry


def _check_auth(request: Request, action: str) -> None:
    """Check if user is authorized for the action."""
    user = getattr(request.state, "user", None)
    auth = getattr(request.app.state, "auth", None)
    if auth and user and not auth.is_allowed(user, action):
        log_audit(action, request.url.path, "denied", user=user.name)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )
    if user:
        log_audit(action, request.url.path, "success", user=user.name)


# ── REST endpoints ─────────────────────────────────────────────────────────────

@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    req: CreateSessionRequest,
    request: Request,
) -> ChatSessionResponse:
    """Create a new chat session."""
    _check_auth(request, ACTION_CHAT_CREATE)
    store = _get_store(request)

    session = store.create_session(
        model=req.model,
        provider=req.provider,
        system_prompt=req.system_prompt,
        title=req.title,
    )

    # Also create a context window for this session/agent
    ctx = _get_ctx(request)
    if req.system_prompt:
        ctx.add_to_window(session.session_id, "system", req.system_prompt)

    logger.info("Created chat session %s (model=%s/%s)",
                session.session_id, req.provider, req.model)

    return ChatSessionResponse(
        session_id=session.session_id,
        title=session.title,
        model=session.model,
        provider=session.provider,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
        message_count=len(store.get_messages(session.session_id)),
    )


@router.get("/sessions")
async def list_sessions(
    request: Request,
    limit: int = 50,
    offset: int = 0,
) -> List[ChatSessionResponse]:
    """List all chat sessions."""
    store = _get_store(request)
    sessions = store.list_sessions(limit=limit, offset=offset)
    return [
        ChatSessionResponse(
            session_id=s.session_id,
            title=s.title,
            model=s.model,
            provider=s.provider,
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
            message_count=len(store.get_messages(s.session_id)),
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
) -> ChatHistoryResponse:
    """Get the full history of a chat session."""
    _check_auth(request, ACTION_CHAT_GET)
    store = _get_store(request)

    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                          detail=f"Session {session_id} not found")

    messages = store.get_messages(session_id)
    return ChatHistoryResponse(
        session_id=session_id,
        title=session.title,
        messages=[
            ChatMessageResponse(
                role=m.role,
                content=m.content,
                timestamp=m.timestamp.isoformat(),
                message_id=m.message_id,
            )
            for m in messages
        ],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    request: Request,
) -> None:
    """End and delete a chat session."""
    _check_auth(request, ACTION_CHAT_DELETE)
    store = _get_store(request)

    if not store.delete_session(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                          detail=f"Session {session_id} not found")

    # Also clear context window
    ctx = _get_ctx(request)
    ctx.clear_window(session_id)

    logger.info("Deleted chat session %s", session_id)


@router.post("/sessions/{session_id}/send")
async def send_message(
    session_id: str,
    req: SendMessageRequest,
    request: Request,
) -> List[ChatMessageResponse]:
    """Send a message and get a non-streaming response."""
    _check_auth(request, ACTION_CHAT_SEND)
    store = _get_store(request)
    ctx = _get_ctx(request)
    registry = _get_registry(request)

    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                          detail=f"Session {session_id} not found")

    if not req.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                          detail="Message cannot be empty")

    # Store user message
    store.add_message(session_id, "user", req.message)
    ctx.add_to_window(session_id, "user", req.message)

    # Build history messages for the model
    messages: List[ChatMessage] = []
    for m in store.get_messages(session_id):
        if m.role == "system":
            messages.append(ChatMessage(role="system", content=m.content))
        elif m.role == "user":
            messages.append(ChatMessage(role="user", content=m.content))
        elif m.role == "assistant":
            messages.append(ChatMessage(role="assistant", content=m.content))

    # Call model
    prov = registry.get(session.provider)
    if prov is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{session.provider}' not available",
        )

    try:
        completion = await prov.chat(messages, model=session.model)
    except Exception as e:
        logger.error("Chat model error for session %s: %s", session_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Model error: {e}",
        )

    # Store assistant response
    store.add_message(session_id, "assistant", completion.content)
    ctx.add_to_window(session_id, "assistant", completion.content)

    # Return the turn (user msg + assistant response)
    history = store.get_messages(session_id)
    return [
        ChatMessageResponse(
            role=m.role,
            content=m.content,
            timestamp=m.timestamp.isoformat(),
            message_id=m.message_id,
        )
        for m in history[-2:]
    ]


@router.post("/sessions/{session_id}/stream")
async def stream_message(
    session_id: str,
    req: SendMessageRequest,
    request: Request,
) -> StreamingResponse:
    """Send a message and stream the response via SSE."""
    _check_auth(request, ACTION_CHAT_SEND)
    store = _get_store(request)
    ctx = _get_ctx(request)
    registry = _get_registry(request)

    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                          detail=f"Session {session_id} not found")

    if not req.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                          detail="Message cannot be empty")

    # Store user message
    store.add_message(session_id, "user", req.message)
    ctx.add_to_window(session_id, "user", req.message)

    async def event_generator():
        content_parts: List[str] = []
        prov = registry.get(session.provider)
        if prov is None:
            yield f"data: {json.dumps({'error': f'Provider {session.provider} not available'})}\n\n"
            return

        messages: List[ChatMessage] = []
        for m in store.get_messages(session_id):
            if m.role in ("system", "user", "assistant"):
                messages.append(ChatMessage(role=m.role, content=m.content))

        try:
            yield f"data: {json.dumps({'type': 'thinking', 'text': '...'})}\n\n"
            async for chunk in prov.stream(messages, model=session.model):
                if chunk.content:
                    content_parts.append(chunk.content)
                    yield f"data: {json.dumps({'type': 'token', 'text': chunk.content})}\n\n"
                if chunk.finish_reason:
                    break
        except Exception as e:
            logger.error("Stream error for session %s: %s", session_id, e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

        # Store complete response
        full_content = "".join(content_parts)
        store.add_message(session_id, "assistant", full_content)
        ctx.add_to_window(session_id, "assistant", full_content)

        yield f"data: {json.dumps({'type': 'done', 'content': full_content})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# ── WebSocket endpoint ───────────────────────────────────────────────────────

@router.websocket("/sessions/{session_id}/ws")
async def chat_websocket(websocket: WebSocket, session_id: str):
    """Bidirectional WebSocket for real-time chat.

    Messages:
      client -> server: {"action": "send", "message": "hi"}
      server -> client: {"type": "token", "text": "h"}
      server -> client: {"type": "token", "text": "e"}
      server -> client: {"type": "done", "content": "hello!"}
    """
    request: Request = websocket.scope.get("request")
    store = _get_store(request)
    ctx = _get_ctx(request)
    registry = _get_registry(request)
    session = store.get_session(session_id)

    if session is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    logger.info("WebSocket opened for session %s", session_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            action = data.get("action", "")
            if action == "send":
                message = data.get("message", "")
                if not message.strip():
                    await websocket.send_json({"type": "error", "message": "Empty message"})
                    continue

                # Store user message
                store.add_message(session_id, "user", message)
                ctx.add_to_window(session_id, "user", message)

                # Stream response
                messages: List[ChatMessage] = []
                for m in store.get_messages(session_id):
                    if m.role in ("system", "user", "assistant"):
                        messages.append(ChatMessage(role=m.role, content=m.content))

                prov = registry.get(session.provider)
                if prov is None:
                    await websocket.send_json({"type": "error",
                                              "message": f"Provider {session.provider} unavailable"})
                    continue

                content_parts: List[str] = []
                try:
                    async for chunk in prov.stream(messages, model=session.model):
                        if chunk.content:
                            content_parts.append(chunk.content)
                            await websocket.send_json({"type": "token", "text": chunk.content})
                        if chunk.finish_reason:
                            break
                except Exception as e:
                    logger.error("WebSocket stream error for session %s: %s", session_id, e)
                    await websocket.send_json({"type": "error", "message": str(e)})
                    continue

                full_content = "".join(content_parts)
                store.add_message(session_id, "assistant", full_content)
                ctx.add_to_window(session_id, "assistant", full_content)

                await websocket.send_json({"type": "done", "content": full_content})

            elif action == "history":
                msgs = store.get_messages(session_id)
                await websocket.send_json({
                    "type": "history",
                    "messages": [
                        {"role": m.role, "content": m.content, "timestamp": m.timestamp.isoformat(),
                         "message_id": m.message_id}
                        for m in msgs
                    ],
                })

            elif action == "close":
                await websocket.close(code=1000, reason="Client closed")
                break

            else:
                await websocket.send_json({"type": "error", "message": f"Unknown action: {action}"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session %s", session_id)
    except Exception as e:
        logger.error("WebSocket error for session %s: %s", session_id, e)
        try:
            await websocket.close(code=1011, reason=str(e)[:100])
        except Exception:
            pass
