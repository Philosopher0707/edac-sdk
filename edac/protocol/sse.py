"""SSE Bridge — Real-time event streaming via Server-Sent Events.

Wraps the event bus into an SSE-compatible stream for web clients.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

from edac.event.bus import EventBus
from edac.event.schema import EventType

logger = logging.getLogger("edac.protocol.sse")


class SSEBridge:
    """Bridge event bus to SSE stream."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    async def stream(self, topics: Optional[list[str]] = None) -> AsyncIterator[str]:
        """Yield SSE-formatted events from the bus."""
        async with self.bus.stream(topics=topics) as events:
            async for event in events:
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
