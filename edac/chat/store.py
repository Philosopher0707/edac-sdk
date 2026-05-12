"""In-memory chat session store for conversation management.

Provides thread-safe CRUD for sessions and messages.  Not persistent —
for persistence, front with a database-backed store later.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class ChatSession:
    session_id: str
    agent_id: str
    title: str
    status: str = "active"
    provider: Optional[str] = None
    model: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    messages: List[ChatMessage] = field(default_factory=list)


class ChatStore:
    """Simple in-memory store for chat sessions."""

    def __init__(self, max_sessions: int = 1000, ttl_seconds: int = 86400) -> None:
        self._sessions: Dict[str, ChatSession] = {}
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds

    def create_session(
        self,
        agent_id: str,
        title: str = "",
        status: str = "active",
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ChatSession:
        """Create a new chat session."""
        sid = str(uuid.uuid4())
        session = ChatSession(
            session_id=sid,
            agent_id=agent_id,
            title=title,
            status=status,
            provider=provider,
            model=model,
        )
        self._sessions[sid] = session
        self._maybe_evict()
        return session

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """Retrieve a session by ID."""
        return self._sessions.get(str(session_id))

    def list_sessions(
        self,
        status: Optional[str] = None,
        limit: int = 0,
        offset: int = 0,
    ) -> List[ChatSession]:
        """List all sessions, optionally filtered by status, with pagination."""
        sessions = list(self._sessions.values())
        if status:
            sessions = [s for s in sessions if s.status == status]
        if offset:
            sessions = sessions[offset:]
        if limit:
            sessions = sessions[:limit]
        return sessions

    def update_session(self, session_id: str, **kwargs) -> Optional[ChatSession]:
        """Update session fields. Returns None if not found."""
        session = self.get_session(session_id)
        if session is None:
            return None
        for key, value in kwargs.items():
            if hasattr(session, key):
                setattr(session, key, value)
        session.updated_at = datetime.now(timezone.utc)
        return session

    def delete_session(self, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        return self._sessions.pop(str(session_id), None) is not None

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> Optional[ChatMessage]:
        """Append a message to a session. Returns None if session not found."""
        session = self.get_session(session_id)
        if session is None:
            return None
        msg = ChatMessage(role=role, content=content, metadata=metadata or {})
        session.messages.append(msg)
        session.updated_at = datetime.now(timezone.utc)
        return msg

    def get_messages(
        self,
        session_id: str,
        offset: int = 0,
        limit: int = 0,
    ) -> List[ChatMessage]:
        """Retrieve messages for a session, with optional pagination."""
        session = self.get_session(session_id)
        if session is None:
            return []
        msgs = session.messages
        if offset:
            msgs = msgs[offset:]
        if limit:
            msgs = msgs[:limit]
        return msgs

    def clear(self) -> None:
        """Remove all sessions."""
        self._sessions.clear()

    async def close(self) -> None:
        """Close the store. In-memory version is a no-op."""
        pass

    def _maybe_evict(self) -> None:
        """Evict oldest sessions if over max capacity."""
        if len(self._sessions) <= self.max_sessions:
            return
        # Sort by updated_at, evict oldest
        sorted_sessions = sorted(self._sessions.values(), key=lambda s: s.updated_at)
        to_evict = len(self._sessions) - self.max_sessions
        for session in sorted_sessions[:to_evict]:
            self._sessions.pop(session.session_id, None)
