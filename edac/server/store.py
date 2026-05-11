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
                retry_count INTEGER NOT NULL DEFAULT 0,
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
        # Dead Letter Queue for failed tasks that exhausted retries
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                goal TEXT NOT NULL DEFAULT '',
                pattern TEXT NOT NULL DEFAULT 'pipeline',
                agents TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                original_created_at TEXT NOT NULL DEFAULT '',
                failed_at TEXT NOT NULL DEFAULT ''
            )
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

    async def count_tasks(self, status: Optional[str] = None) -> int:
        """Return the total number of tasks (optionally filtered by status)."""
        if status:
            query = "SELECT COUNT(*) FROM tasks WHERE status = ?"
            params = (status,)
        else:
            query = "SELECT COUNT(*) FROM tasks"
            params = ()
        async with self._db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

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

    async def count_tasks(self, status: Optional[str] = None) -> int:
        """Return total number of tasks, optionally filtered by status."""
        if status:
            query = "SELECT COUNT(*) FROM tasks WHERE status = ?"
            params = (status,)
        else:
            query = "SELECT COUNT(*) FROM tasks"
            params = ()
        async with self._db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

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

    async def increment_retry(self, task_id: str) -> int:
        """Increment retry count, return new value."""
        await self._db.execute(
            "UPDATE tasks SET retry_count = retry_count + 1 WHERE id = ?",
            (task_id,),
        )
        await self._db.commit()
        async with self._db.execute(
            "SELECT retry_count FROM tasks WHERE id = ?", (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["retry_count"] if row else 0

    async def move_to_dlq(self, task_id: str, max_retries: int = 3) -> bool:
        """Move a failed task to DLQ if retries exhausted."""
        task = await self.get_task(task_id)
        if task is None:
            return False
        retry_count = await self.increment_retry(task_id)
        if retry_count >= max_retries:
            now = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """
                INSERT OR REPLACE INTO dead_letter
                (task_id, goal, pattern, agents, error, retry_count, original_created_at, failed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task.goal,
                    task.pattern,
                    json.dumps(task.agents),
                    task.error,
                    retry_count,
                    task.created_at,
                    now,
                ),
            )
            await self._db.execute(
                "UPDATE tasks SET status = 'dead_letter' WHERE id = ?", (task_id,)
            )
            await self._db.commit()
            return True
        return False

    async def count_dlq(self) -> int:
        """Return the total number of dead-letter queue entries."""
        async with self._db.execute("SELECT COUNT(*) FROM dead_letter") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def list_dlq(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        async with self._db.execute(
            "SELECT * FROM dead_letter ORDER BY failed_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "goal": row["goal"],
                    "pattern": row["pattern"],
                    "agents": json.loads(row["agents"]),
                    "error": row["error"],
                    "retry_count": row["retry_count"],
                    "failed_at": row["failed_at"],
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


class PostgresTaskStore:
    """PostgreSQL-backed task store."""

    def __init__(
        self,
        database_url: str = "postgresql://localhost/edac",
        pool_size: int = 5,
    ):
        self.database_url = database_url
        self.pool_size = pool_size
        self._pool: Optional[Any] = None

    async def connect(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=self.pool_size,
        )
        await self._migrate()
        logger.info(f"PostgresTaskStore connected: {self.database_url}")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("PostgresTaskStore closed")

    async def _migrate(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    goal TEXT NOT NULL DEFAULT '',
                    pattern TEXT NOT NULL DEFAULT 'pipeline',
                    agents TEXT NOT NULL DEFAULT '[]',
                    result TEXT,
                    error TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    timestamp TEXT NOT NULL DEFAULT ''
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id)
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS dead_letter (
                    id SERIAL PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    goal TEXT NOT NULL DEFAULT '',
                    pattern TEXT NOT NULL DEFAULT 'pipeline',
                    agents TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    original_created_at TEXT NOT NULL DEFAULT '',
                    failed_at TEXT NOT NULL DEFAULT ''
                )
            """)

    async def create_task(
        self,
        task_id: str,
        goal: str,
        pattern: str = "pipeline",
        agents: Optional[List[Dict[str, Any]]] = None,
    ) -> TaskRecord:
        now = datetime.now(timezone.utc).isoformat()
        agents_json = json.dumps(agents or [])
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tasks (id, status, goal, pattern, agents, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                task_id, "pending", goal, pattern, agents_json, now, now,
            )
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
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id = $1", task_id
            )
            if row is None:
                return None
            return self._pg_row_to_task(row)

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        result_json = json.dumps(result) if result is not None else None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tasks SET status = $1, result = $2, error = $3, updated_at = $4
                WHERE id = $5
                """,
                status, result_json, error, now, task_id,
            )

    async def list_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TaskRecord]:
        async with self._pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    """
                    SELECT * FROM tasks WHERE status = $1
                    ORDER BY created_at DESC LIMIT $2 OFFSET $3
                    """,
                    status, limit, offset,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM tasks ORDER BY created_at DESC LIMIT $1 OFFSET $2
                    """,
                    limit, offset,
                )
            return [self._pg_row_to_task(row) for row in rows]

    async def count_tasks(self, status: Optional[str] = None) -> int:
        """Return total number of tasks, optionally filtered by status."""
        async with self._pool.acquire() as conn:
            if status:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) FROM tasks WHERE status = $1", status
                )
            else:
                row = await conn.fetchrow("SELECT COUNT(*) FROM tasks")
            return row["count"] if row else 0

    async def add_event(self, task_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO events (task_id, event_type, payload, timestamp)
                VALUES ($1, $2, $3, $4)
                """,
                task_id, event_type, json.dumps(payload), now,
            )

    async def get_events(self, task_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM events WHERE task_id = $1 ORDER BY timestamp",
                task_id,
            )
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

    async def increment_retry(self, task_id: str) -> int:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE tasks SET retry_count = retry_count + 1 WHERE id = $1",
                task_id,
            )
            row = await conn.fetchrow(
                "SELECT retry_count FROM tasks WHERE id = $1", task_id
            )
            return row["retry_count"] if row else 0

    async def move_to_dlq(self, task_id: str, max_retries: int = 3) -> bool:
        task = await self.get_task(task_id)
        if task is None:
            return False
        retry_count = await self.increment_retry(task_id)
        if retry_count >= max_retries:
            now = datetime.now(timezone.utc).isoformat()
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO dead_letter
                    (task_id, goal, pattern, agents, error, retry_count, original_created_at, failed_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (task_id) DO UPDATE SET
                        goal = EXCLUDED.goal,
                        pattern = EXCLUDED.pattern,
                        agents = EXCLUDED.agents,
                        error = EXCLUDED.error,
                        retry_count = EXCLUDED.retry_count,
                        original_created_at = EXCLUDED.original_created_at,
                        failed_at = EXCLUDED.failed_at
                    """,
                    task_id,
                    task.goal,
                    task.pattern,
                    json.dumps(task.agents),
                    task.error,
                    retry_count,
                    task.created_at,
                    now,
                )
                await conn.execute(
                    "UPDATE tasks SET status = 'dead_letter' WHERE id = $1",
                    task_id,
                )
            return True
        return False

    async def list_dlq(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM dead_letter ORDER BY failed_at DESC LIMIT $1 OFFSET $2
                """,
                limit, offset,
            )
            return [
                {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "goal": row["goal"],
                    "pattern": row["pattern"],
                    "agents": json.loads(row["agents"]),
                    "error": row["error"],
                    "retry_count": row["retry_count"],
                    "failed_at": row["failed_at"],
                }
                for row in rows
            ]

    async def count_dlq(self) -> int:
        """Return total number of dead letter queue entries."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) FROM dead_letter")
            return row["count"] if row else 0

    def _pg_row_to_task(self, row) -> TaskRecord:
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


def create_store(database_url: str, pool_size: int = 5) -> TaskStore:
    """Factory for task stores."""
    if database_url.startswith("sqlite+") or database_url.startswith("sqlite:"):
        return TaskStore(database_url=database_url)
    if database_url.startswith("postgresql://") or database_url.startswith("postgres://"):
        return PostgresTaskStore(database_url=database_url, pool_size=pool_size)
    raise ValueError(f"Unsupported database URL: {database_url}")
