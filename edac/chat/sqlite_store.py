"""SQLite-backed chat session store with persistence across restarts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiosqlite

from edac.chat.store import ChatMessage, ChatSession, ChatStore

logger = logging.getLogger("edac.chat.sqlite_store")


class SqliteChatStore(ChatStore):
    """ChatStore backed by SQLite for persistent sessions and messages."""

    def __init__(self, db_path: str = "data/chat.db") -> None:
        super().__init__()
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS chat_sessions (
            session_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            provider TEXT,
            model TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
        )
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES chat_sessions(session_id)
        )"""
        )
        await self._db.commit()
        logger.info("SqliteChatStore connected to %s", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── Session CRUD ──

    def create_session(
        self,
        agent_id: str,
        title: str = "",
        status: str = "active",
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ChatSession:
        import uuid

        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        session = ChatSession(
            session_id=sid,
            agent_id=agent_id,
            title=title,
            status=status,
            provider=provider,
            model=model,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self._sessions[sid] = session
        return session

    async def persist_session(self, session: ChatSession) -> None:
        """Write session to SQLite (call after create_session for persistence)."""
        if not self._db:
            return
        await self._db.execute(
            "INSERT OR REPLACE INTO chat_sessions VALUES (?,?,?,?,?,?,?,?)",
            (
                session.session_id,
                session.agent_id,
                session.title,
                session.status,
                session.provider,
                session.model,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
            ),
        )
        await self._db.commit()

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        return self._sessions.get(str(session_id))

    def list_sessions(
        self,
        status: Optional[str] = None,
        limit: int = 0,
        offset: int = 0,
    ) -> List[ChatSession]:
        sessions = list(self._sessions.values())
        if status:
            sessions = [s for s in sessions if s.status == status]
        if offset:
            sessions = sessions[offset:]
        if limit:
            sessions = sessions[:limit]
        return sessions

    def delete_session(self, session_id: str) -> bool:
        sid = str(session_id)
        if sid in self._sessions:
            del self._sessions[sid]
            # Also delete from SQLite
            if self._db:
                import asyncio

                asyncio.create_task(self._delete_from_db(sid))
            return True
        return False

    async def _delete_from_db(self, session_id: str) -> None:
        if self._db:
            await self._db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            await self._db.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
            await self._db.commit()

    # ── Messages ──

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> Optional[ChatMessage]:
        session = self.get_session(session_id)
        if session is None:
            return None
        msg = ChatMessage(role=role, content=content, metadata=metadata or {})
        session.messages.append(msg)
        session.updated_at = datetime.now(timezone.utc)
        # Persist to SQLite
        if self._db:
            import asyncio

            asyncio.create_task(self._persist_message(session_id, msg))
        return msg

    async def _persist_message(self, session_id: str, msg: ChatMessage) -> None:
        if self._db:
            await self._db.execute(
                "INSERT INTO chat_messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                (session_id, msg.role, msg.content, msg.timestamp.isoformat()),
            )
            await self._db.commit()

    def get_messages(self, session_id: str, offset: int = 0, limit: int = 0) -> List[ChatMessage]:
        session = self.get_session(session_id)
        if session is None:
            return []
        msgs = session.messages
        if offset:
            msgs = msgs[offset:]
        if limit:
            msgs = msgs[:limit]
        return msgs

    # ── Load from disk ──

    async def load_from_db(self) -> int:
        """Load all sessions and messages from SQLite into memory. Returns count."""
        if not self._db:
            return 0
        count = 0
        async with self._db.execute(
            "SELECT * FROM chat_sessions ORDER BY updated_at DESC"
        ) as cursor:
            async for row in cursor:
                session = ChatSession(
                    session_id=row["session_id"],
                    agent_id=row["agent_id"],
                    title=row["title"],
                    status=row["status"],
                    provider=row["provider"],
                    model=row["model"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
                # Load messages
                async with self._db.execute(
                    "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id",
                    (session.session_id,),
                ) as mcursor:
                    async for mrow in mcursor:
                        session.messages.append(
                            ChatMessage(
                                role=mrow["role"],
                                content=mrow["content"],
                                timestamp=datetime.fromisoformat(mrow["timestamp"]),
                            )
                        )
                self._sessions[session.session_id] = session
                count += 1
        logger.info("Loaded %d chat sessions from %s", count, self.db_path)
        return count
