"""Synchronous wrapper around the async :class:`~edac.client.client.EdacClient`.

Runs a private event loop in a background daemon thread so that blocking code
(e.g. Jupyter notebooks, shell scripts, ML pipelines) can use the EDAC SDK
without ``asyncio.run()`` boilerplate.

Usage::

    from edac.client.sync_client import EdacClientSync

    client = EdacClientSync("http://localhost:8000", api_key="sekrit")
    agents = client.list_agents()
    client.close()

Or as a context manager::

    with EdacClientSync("http://localhost:8000") as client:
        health = client.get_health()
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List, Optional, Tuple, Union

from httpx import Limits

from edac.client.client import EdacClient
from edac.client.retry import RetryConfig
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


class EdacClientSync:
    """Thread-safe synchronous client for the EDAC HTTP API."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        limits: Optional[Limits] = None,
        retry: Optional[RetryConfig] = None,
        http2: bool = False,
    ) -> None:
        self._async_client = EdacClient(
            base_url,
            api_key=api_key,
            timeout=timeout,
            limits=limits,
            retry=retry,
            http2=http2,
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    # ── Internal helpers ──

    def _run(self, coro):
        """Schedule *coro* on the background loop and block for the result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    # ── Agent CRUD ──

    def create_agent(self, request: CreateAgentRequest) -> AgentInfo:
        return self._run(self._async_client.create_agent(request))

    def list_agents(self, *, limit: Optional[int] = None, offset: Optional[int] = None) -> PaginatedList[AgentInfo]:
        return self._run(self._async_client.list_agents(limit=limit, offset=offset))

    def get_agent(self, agent_id: str) -> AgentInfo:
        return self._run(self._async_client.get_agent(agent_id))

    def delete_agent(self, agent_id: str) -> Dict[str, str]:
        return self._run(self._async_client.delete_agent(agent_id))

    # ── Agent lifecycle ──

    def restart_agent(self, agent_id: str) -> AgentInfo:
        return self._run(self._async_client.restart_agent(agent_id))

    def pause_agent(self, agent_id: str) -> AgentInfo:
        return self._run(self._async_client.pause_agent(agent_id))

    def resume_agent(self, agent_id: str) -> AgentInfo:
        return self._run(self._async_client.resume_agent(agent_id))

    # ── Batch operations ──

    def create_agents_batch(self, requests: List[CreateAgentRequest]) -> List[Union[AgentInfo, BatchError]]:
        return self._run(self._async_client.create_agents_batch(requests))

    def delete_agents_batch(self, agent_ids: List[str]) -> List[Dict[str, str]]:
        return self._run(self._async_client.delete_agents_batch(agent_ids))

    # ── Tasks ──

    def submit_task(self, request: SubmitTaskRequest) -> TaskResponse:
        return self._run(self._async_client.submit_task(request))

    def list_tasks(
        self,
        *,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> PaginatedList[TaskResponse]:
        return self._run(self._async_client.list_tasks(status=status, limit=limit, offset=offset))

    def get_task(self, task_id: str) -> TaskResponse:
        return self._run(self._async_client.get_task(task_id))

    def cancel_task(self, task_id: str) -> TaskResponse:
        return self._run(self._async_client.cancel_task(task_id))

    def get_task_events(self, task_id: str) -> List[Dict[str, Any]]:
        return self._run(self._async_client.get_task_events(task_id))

    def submit_tasks_batch(self, requests: List[SubmitTaskRequest]) -> List[Union[TaskResponse, BatchError]]:
        return self._run(self._async_client.submit_tasks_batch(requests))

    # ── System ──

    def get_health(self) -> HealthResponse:
        return self._run(self._async_client.get_health())

    # ── Streaming (sync generator via background queue) ──

    def stream_events(self, *, topics: Optional[List[str]] = None, timeout: float = 30.0):
        """Yield SSE events synchronously.

        This is a blocking generator that runs the async SSE consumer in the
        background loop and yields decoded event dictionaries.
        """
        yield from self._async_generator_to_sync(
            self._async_client.stream_events(topics=topics, timeout=timeout),
            timeout=timeout,
        )

    def watch_task(self, task_id: str, *, timeout: float = 30.0):
        """Yield real-time task updates via WebSocket (sync).

        Requires the ``websockets`` package (``pip install edac[stream]``).
        """
        yield from self._async_generator_to_sync(
            self._async_client.watch_task(task_id, timeout=timeout),
            timeout=timeout,
        )

    def _async_generator_to_sync(self, async_gen, *, timeout: float = 30.0):
        """Bridge an async generator into a sync generator using a queue."""
        import queue

        q: queue.Queue = queue.Queue()
        stop_event = threading.Event()

        async def _pump() -> None:
            try:
                async for item in async_gen:
                    q.put(("item", item))
                    if stop_event.is_set():
                        break
            except Exception as exc:
                q.put(("error", exc))
            finally:
                q.put(("done", None))

        future = asyncio.run_coroutine_threadsafe(_pump(), self._loop)

        try:
            while True:
                kind, payload = q.get(timeout=timeout + 5)
                if kind == "item":
                    yield payload
                elif kind == "error":
                    raise payload
                elif kind == "done":
                    break
        finally:
            stop_event.set()
            try:
                future.result(timeout=2)
            except Exception:
                pass

    # ── Context manager ──

    def close(self) -> None:
        """Shut down the background event loop and release resources."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            if self._thread.is_alive():
                self._run(self._async_client.close())
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def __enter__(self) -> EdacClientSync:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
