"""Task persistence — SQLite with aiosqlite.

Tables:
- tasks: id, status, goal, pattern, agents, result, created_at, updated_at
- events: id, task_id, event_type, payload, timestamp
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger("edac.server.store")


@dataclass
class TaskRecord:
    """A persisted task."""
    id: str
    status: str = "pending"
    goal: str = ""
    pattern: str = "pipeline"
    agents: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


class TaskStore:
    """SQLite-backed task store."""

    def __init__(self, database_url: str = "sqlite+aiosqlite:///./edac.db"):
        self.database_url = database_url.replace("sqlite+aiosqlite:///", "")
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.database_url)
        self._db.row_factory = aiosqlite.Row
        await self._migrate()
        logger.info(f"TaskStore connected: {self.database_url}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("TaskStore closed")

    async def _migrate(self) -> None:
        """Create tables if they don't exist."""
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                goal TEXT NOT NULL DEFAULT '',
                pattern TEXT NOT NULL DEFAULT 'pipeline',
                agents TEXT NOT NULL DEFAULT '[]',
                result TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                timestamp TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id)
        """)
        await self._db.commit()

    async def create_task(
        self,
        task_id: str,
        goal: str,
        pattern: str = "pipeline",
        agents: Optional[List[Dict[str, Any]]] = None,
    ) -> TaskRecord:
        now = datetime.now(timezone.utc).isoformat()
        agents_json = json.dumps(agents or [])
        await self._db.execute(
            "INSERT INTO tasks (id, status, goal, pattern, agents, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, "pending", goal, pattern, agents_json, now, now),
        )
        await self._db.commit()
        return TaskRecord(
            id=task_id,
            status="pending",
            goal=goal,
            pattern=pattern,
            agents=agents or [],
            created_at=now,
            updated_at=now,
        )

    async def get_task(self, task_id: str) -> Optional[TaskRecord]:
        async with self._db.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_task(row)

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        result_json = json.dumps(result) if result is not None else None
        await self._db.execute(
            "UPDATE tasks SET status = ?, result = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, result_json, error, now, task_id),
        )
        await self._db.commit()

    async def list_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TaskRecord]:
        if status:
            query = "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params = (status, limit, offset)
        else:
            query = "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params = (limit, offset)

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_task(row) for row in rows]

    async def add_event(self, task_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO events (task_id, event_type, payload, timestamp) VALUES (?, ?, ?, ?)",
            (task_id, event_type, json.dumps(payload), now),
        )
        await self._db.commit()

    async def get_events(self, task_id: str) -> List[Dict[str, Any]]:
        async with self._db.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY timestamp",
            (task_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload"]),
                    "timestamp": row["timestamp"],
                }
                for row in rows
            ]

    def _row_to_task(self, row: aiosqlite.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            status=row["status"],
            goal=row["goal"],
            pattern=row["pattern"],
            agents=json.loads(row["agents"]),
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
