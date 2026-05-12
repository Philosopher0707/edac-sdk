"""Streaming — SSE server for real-time event delivery to clients."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from edac.event.schema import Event

logger = logging.getLogger("edac.human.stream")


class EventStream:
    """In-memory event stream with async iteration."""

    def __init__(self, max_buffer: int = 1000) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_buffer)
        self._closed = False

    async def emit(self, event: Event) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop oldest
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(event)

    async def close(self) -> None:
        self._closed = True

    async def __aiter__(self) -> AsyncIterator[Event]:
        while not self._closed:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                continue


class SSEStream:
    """SSE-compatible event stream formatter."""

    def __init__(self, stream: EventStream) -> None:
        self.stream = stream

    async def __aiter__(self) -> AsyncIterator[str]:
        async for event in self.stream:
            data = json.dumps(
                {
                    "event_type": event.event_type.value,
                    "source": event.source,
                    "topic": event.topic,
                    "payload": event.payload,
                    "timestamp": event.timestamp.isoformat(),
                }
            )
            yield f"data: {data}\n\n"
