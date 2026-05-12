"""Webhook delivery system for EDAC task lifecycle events.

When a task reaches a terminal status (completed, failed, cancelled), any
registered webhooks for that task are POSTed the task result as JSON.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("edac.server.webhook")


@dataclass
class WebhookConfig:
    """Configuration for a webhook endpoint."""

    url: str
    events: List[str] = field(default_factory=lambda: ["task.completed", "task.failed"])
    secret: Optional[str] = None  # For HMAC signature verification (future)


@dataclass
class WebhookDelivery:
    """Record of a single webhook delivery attempt."""

    url: str
    task_id: str
    status: str
    payload: Dict[str, Any]
    attempt: int
    response_status: Optional[int] = None
    delivered_at: Optional[float] = None
    error: Optional[str] = None


class WebhookDispatcher:
    """Async webhook delivery with retries."""

    def __init__(self, max_retries: int = 3, backoff_base: float = 1.0) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def close(self) -> None:
        await self._client.aclose()

    async def deliver(
        self,
        config: WebhookConfig,
        task_id: str,
        payload: Dict[str, Any],
    ) -> WebhookDelivery:
        """Deliver a webhook with retries.

        Returns a :class:`WebhookDelivery` regardless of success or failure.
        """
        status = payload.get("status", "unknown")
        last_error: Optional[str] = None
        last_status: Optional[int] = None

        for attempt in range(1, self._max_retries + 1):
            try:
                resp = await self._client.post(
                    config.url,
                    json={
                        "task_id": task_id,
                        "status": status,
                        "payload": payload,
                        "timestamp": time.time(),
                    },
                    headers={"Content-Type": "application/json"},
                )
                last_status = resp.status_code
                if resp.status_code < 500:
                    return WebhookDelivery(
                        url=config.url,
                        task_id=task_id,
                        status=status,
                        payload=payload,
                        attempt=attempt,
                        response_status=resp.status_code,
                        delivered_at=time.time(),
                    )
                # 5xx → retry
                last_error = f"HTTP {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Webhook delivery failed (attempt %d/%d): %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
            if attempt < self._max_retries:
                wait = min(self._backoff_base * (2 ** (attempt - 1)), 30.0)
                await asyncio.sleep(wait)

        return WebhookDelivery(
            url=config.url,
            task_id=task_id,
            status=status,
            payload=payload,
            attempt=self._max_retries,
            response_status=last_status,
            delivered_at=time.time(),
            error=last_error,
        )


class TaskWebhookRegistry:
    """In-memory registry of webhooks per task."""

    def __init__(self) -> None:
        self._webhooks: Dict[str, List[WebhookConfig]] = {}

    def register(self, task_id: str, config: WebhookConfig) -> None:
        """Register a webhook for a task."""
        self._webhooks.setdefault(task_id, []).append(config)
        logger.info("Registered webhook %s for task %s", config.url, task_id)

    def get(self, task_id: str) -> List[WebhookConfig]:
        """Get all webhooks registered for a task."""
        return list(self._webhooks.get(task_id, []))

    def remove(self, task_id: str, url: Optional[str] = None) -> bool:
        """Remove webhooks for a task. If *url* is given, only remove matching ones."""
        if task_id not in self._webhooks:
            return False
        if url:
            before = len(self._webhooks[task_id])
            self._webhooks[task_id] = [c for c in self._webhooks[task_id] if c.url != url]
            after = len(self._webhooks[task_id])
            if after == 0:
                del self._webhooks[task_id]
            return after < before
        del self._webhooks[task_id]
        return True

    def list_all(self) -> Dict[str, List[WebhookConfig]]:
        """Return a shallow copy of all registrations."""
        return {k: list(v) for k, v in self._webhooks.items()}
