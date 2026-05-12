"""Async HTTP client for the EDAC production server API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

import httpx
from httpx import Limits

from edac.client.exceptions import (
    EdacAPIError,
    EdacAuthError,
    EdacClientError,
    EdacNotFoundError,
    EdacRetryExhausted,
    EdacStreamError,
)
from edac.client.retry import RetryConfig, _should_retry, _RETRY_NETWORK_ERRORS
from edac.server.schemas import (
    AgentInfo,
    BatchError,
    CreateAgentRequest,
    HealthResponse,
    PaginatedList,
    SubmitTaskRequest,
    TaskResponse,
    TaskUpdate,
)

logger = logging.getLogger("edac.client")


def _default_limits(keepalive_expiry: float = 5.0) -> Limits:
    return Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=keepalive_expiry,
    )


class EdacClient:
    """
    Async HTTP client for EDAC.

    Usage::

        async with EdacClient("http://localhost:8000", api_key="sekrit") as client:
            agent = await client.create_agent(CreateAgentRequest(name="worker"))
            agents = await client.list_agents()
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        limits: Optional[Limits] = None,
        retry: Optional[RetryConfig] = None,
        http2: bool = False,
        keepalive_expiry: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._retry = retry or RetryConfig()
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            limits=limits or _default_limits(keepalive_expiry),
            http2=http2,
            headers=self._headers(),
        )
        self._closed = False

    def __repr__(self) -> str:
        api_key_hint = "***" if self.api_key else None
        return (
            f"<EdacClient(base_url={self.base_url!r}, "
            f"api_key={api_key_hint!r}, timeout={self._client.timeout})>"
        )

    # ── Internal helpers ──

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _handle_error(self, response: httpx.Response) -> None:
        """Raise typed exceptions for known error codes."""
        try:
            payload = response.json()
        except Exception:
            payload = {"detail": response.text or "Unknown error"}

        detail = payload.get("detail", "Unknown error")
        request_id = payload.get("request_id") or response.headers.get("x-request-id")
        kwargs = {"status_code": response.status_code, "response": payload, "request_id": request_id}
        if response.status_code == 404:
            raise EdacNotFoundError(detail, **kwargs)
        if response.status_code in (401, 403):
            raise EdacAuthError(detail, **kwargs)
        raise EdacAPIError(detail, **kwargs)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """Execute an HTTP request with automatic retry/backoff."""
        t0 = time.monotonic()
        last_exc: Optional[BaseException] = None
        for attempt in range(self._retry.max_retries + 1):
            try:
                if stream:
                    response = await self._client.request(
                        method, path, params=params, json=json, **kwargs
                    )
                else:
                    response = await self._client.request(
                        method, path, params=params, json=json, **kwargs
                    )
                logger.debug(
                    '%s %s -> %d (%.1f ms)',
                    method, path, response.status_code, (time.monotonic() - t0) * 1000
                )
                return response
            except BaseException as exc:
                last_exc = exc
                if attempt == self._retry.max_retries or not _should_retry(exc, self._retry):
                    raise
                wait = min(self._retry.backoff_base * (2 ** attempt), self._retry.backoff_max)
                logger.debug(
                    "Retrying %s %s in %.2fs (attempt %d/%d): %s",
                    method, path, wait, attempt + 1, self._retry.max_retries, exc,
                )
                await asyncio.sleep(wait)

        assert last_exc is not None
        status_code = getattr(last_exc, "status_code", None)
        raise EdacRetryExhausted(
            f"All {self._retry.max_retries} retry attempts exhausted: {last_exc}",
            last_status_code=status_code,
            attempts=self._retry.max_retries,
        ) from last_exc

    @staticmethod
    def _parse_paginated(response: httpx.Response) -> Tuple[List[Any], int, int, int]:
        """Extract items + pagination metadata from response headers."""
        items = response.json()
        total = int(response.headers.get("X-Total-Count", "0"))
        limit = int(response.headers.get("X-Limit", "0"))
        offset = int(response.headers.get("X-Offset", "0"))
        return items, total, limit, offset

    # ── Agent CRUD ──

    async def create_agent(self, request: CreateAgentRequest) -> AgentInfo:
        """Create and start a new agent."""
        response = await self._request(
            "POST", "/agents", json=request.model_dump(mode="json", exclude_none=True)
        )
        if response.status_code != 201:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    async def list_agents(
        self,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> PaginatedList[AgentInfo]:
        """List all active agents (paginated)."""
        params: Dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        response = await self._request("GET", "/agents", params=params or None)
        if response.status_code != 200:
            self._handle_error(response)
        items_raw, total, limit_out, offset_out = self._parse_paginated(response)
        items = [AgentInfo.model_validate(item) for item in items_raw]
        return PaginatedList(items=items, total=total, limit=limit_out, offset=offset_out)

    async def get_agent(self, agent_id: str) -> AgentInfo:
        """Get information about a single agent."""
        response = await self._request("GET", f"/agents/{agent_id}")
        if response.status_code != 200:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    async def delete_agent(self, agent_id: str) -> Dict[str, str]:
        """Terminate and remove an agent."""
        response = await self._request("DELETE", f"/agents/{agent_id}")
        if response.status_code != 200:
            self._handle_error(response)
        return response.json()

    # ── Agent lifecycle ──

    async def restart_agent(self, agent_id: str) -> AgentInfo:
        """Restart an agent, preserving its configuration."""
        response = await self._request("POST", f"/agents/{agent_id}/restart")
        if response.status_code != 200:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    async def pause_agent(self, agent_id: str) -> AgentInfo:
        """Pause a running agent."""
        response = await self._request("POST", f"/agents/{agent_id}/pause")
        if response.status_code != 200:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    async def resume_agent(self, agent_id: str) -> AgentInfo:
        """Resume a paused agent."""
        response = await self._request("POST", f"/agents/{agent_id}/resume")
        if response.status_code != 200:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    # ── Batch operations ──

    async def create_agents_batch(
        self, requests: List[CreateAgentRequest]
    ) -> List[Union[AgentInfo, BatchError]]:
        """Create multiple agents in a single round-trip."""
        payload = [r.model_dump(mode="json", exclude_none=True) for r in requests]
        response = await self._request("POST", "/agents/batch", json=payload)
        if response.status_code != 200:
            self._handle_error(response)
        results: List[Union[AgentInfo, BatchError]] = []
        for idx, item in enumerate(response.json()):
            if "error" in item:
                results.append(BatchError.model_validate({**item, "index": idx}))
            else:
                results.append(AgentInfo.model_validate(item))
        return results

    async def delete_agents_batch(self, agent_ids: List[str]) -> List[Dict[str, str]]:
        """Delete multiple agents in a single round-trip."""
        response = await self._request("DELETE", "/agents/batch", json={"agent_ids": agent_ids})
        if response.status_code != 200:
            self._handle_error(response)
        return response.json()

    # ── Tasks ──

    async def submit_task(self, request: SubmitTaskRequest) -> TaskResponse:
        """Submit a new task to the system."""
        response = await self._request(
            "POST", "/tasks", json=request.model_dump(mode="json", exclude_none=True)
        )
        if response.status_code != 200:
            self._handle_error(response)
        return TaskResponse.model_validate(response.json())

    async def list_tasks(
        self,
        *,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> PaginatedList[TaskResponse]:
        """List tasks with optional filtering and pagination."""
        params: Dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        response = await self._request("GET", "/tasks", params=params or None)
        if response.status_code != 200:
            self._handle_error(response)
        items_raw, total, limit_out, offset_out = self._parse_paginated(response)
        items = [TaskResponse.model_validate(item) for item in items_raw]
        return PaginatedList(items=items, total=total, limit=limit_out, offset=offset_out)

    async def get_task(self, task_id: str) -> TaskResponse:
        """Get a single task by ID."""
        response = await self._request("GET", f"/tasks/{task_id}")
        if response.status_code != 200:
            self._handle_error(response)
        return TaskResponse.model_validate(response.json())

    async def cancel_task(self, task_id: str) -> TaskResponse:
        """Cancel a running or queued task."""
        response = await self._request("DELETE", f"/tasks/{task_id}/cancel")
        if response.status_code != 200:
            self._handle_error(response)
        return TaskResponse.model_validate(response.json())

    async def get_task_events(self, task_id: str) -> List[Dict[str, Any]]:
        """Get all events associated with a task."""
        response = await self._request("GET", f"/tasks/{task_id}/events")
        if response.status_code != 200:
            self._handle_error(response)
        return response.json()

    async def submit_tasks_batch(
        self, requests: List[SubmitTaskRequest]
    ) -> List[Union[TaskResponse, BatchError]]:
        """Submit multiple tasks in a single round-trip."""
        payload = [r.model_dump(mode="json", exclude_none=True) for r in requests]
        response = await self._request("POST", "/tasks/batch", json=payload)
        if response.status_code != 200:
            self._handle_error(response)
        results: List[Union[TaskResponse, BatchError]] = []
        for idx, item in enumerate(response.json()):
            if "error" in item:
                results.append(BatchError.model_validate({**item, "index": idx}))
            else:
                results.append(TaskResponse.model_validate(item))
        return results

    # ── Streaming ──

    async def stream_events(
        self,
        *,
        topics: Optional[List[str]] = None,
        timeout: float = 30.0,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Consume the server SSE stream.

        Yields decoded event dictionaries (the ``data:`` payload). The caller
        is responsible for breaking out of the loop; the stream will keep
        running until the server closes the connection.
        """
        params: Dict[str, Any] = {}
        if topics:
            params["topics"] = ",".join(topics)
        try:
            async with self._client.stream(
                "GET", "/events/stream", params=params or None, timeout=timeout
            ) as response:
                if response.status_code != 200:
                    self._handle_error(response)
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError as exc:
                            logger.warning("Ignoring malformed SSE line: %s", exc)
                    elif line.startswith("event: "):
                        # event type line — could be stored if needed
                        pass
                    elif line.startswith("id: "):
                        # event id line
                        pass
        except (_RETRY_NETWORK_ERRORS + (EdacAPIError,)) as exc:
            raise EdacStreamError(f"SSE stream failed: {exc}") from exc

    # ── WebSocket ──

    async def watch_task(
        self,
        task_id: str,
        *,
        timeout: float = 30.0,
    ) -> AsyncIterator[TaskUpdate]:
        """Open a WebSocket to a task and yield real-time updates.

        Requires the ``websockets`` package (``pip install edac[stream]``).
        """
        try:
            import websockets
        except ImportError as exc:
            raise EdacClientError(
                "watch_task requires 'websockets'. Install with: pip install edac[stream]"
            ) from exc

        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        uri = f"{ws_url}/tasks/{task_id}/ws"

        try:
            async with websockets.connect(uri, extra_headers=self._headers()) as ws:
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        msg = json.loads(raw) if isinstance(raw, str) else raw
                        yield TaskUpdate.model_validate(msg)
                    except asyncio.TimeoutError:
                        await ws.ping()
        except Exception as exc:
            raise EdacStreamError(f"WebSocket stream failed for task {task_id}: {exc}") from exc

    # ── Waiting ──

    async def wait_for_task(
        self,
        task_id: str,
        *,
        poll_interval: float = 1.0,
        timeout: float = 60.0,
    ) -> TaskResponse:
        """Poll ``get_task`` until the task reaches a terminal status.

        Args:
            task_id: The task to wait for.
            poll_interval: Seconds between polls (default 1.0).
            timeout: Maximum seconds to wait (default 60.0).

        Returns:
            The final :class:`TaskResponse`.

        Raises:
            EdacClientError: If *timeout* is exceeded.
        """
        terminal = {"completed", "failed", "cancelled"}
        deadline = time.monotonic() + timeout
        while True:
            task = await self.get_task(task_id)
            logger.debug("wait_for_task %s: status=%s", task_id, task.status)
            if task.status in terminal:
                return task
            if time.monotonic() > deadline:
                raise EdacClientError(
                    f"Timeout waiting for task {task_id} after {timeout}s "
                    f"(last status: {task.status})"
                )
            await asyncio.sleep(poll_interval)

    # ── System ──

    async def get_health(self) -> HealthResponse:
        """Fetch the server health status."""
        response = await self._request("GET", "/health")
        if response.status_code != 200:
            self._handle_error(response)
        return HealthResponse.model_validate(response.json())

    # ── Context manager ──

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    async def __aenter__(self) -> EdacClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
