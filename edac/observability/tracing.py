"""OpenTelemetry-compatible tracing for events and agent decisions.

Spans represent operations; events represent state changes within spans.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Generator, List, Optional

from edac.event.schema import Event

logger = logging.getLogger("edac.observability.tracing")


@dataclass
class Span:
    """A trace span."""

    name: str
    trace_id: str
    span_id: str
    parent_id: Optional[str] = None
    start_time: float = 0.0
    end_time: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self.events.append(
            {
                "name": name,
                "timestamp": time.time(),
                "attributes": attributes or {},
            }
        )

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def finish(self) -> None:
        self.end_time = time.time()


class Tracer:
    """Simple tracer with span stack."""

    def __init__(self) -> None:
        self._spans: List[Span] = []
        self._finished: List[Span] = []
        self._trace_counter = 0

    def start_span(self, name: str, parent: Optional[Span] = None) -> Span:
        self._trace_counter += 1
        span = Span(
            name=name,
            trace_id=parent.trace_id if parent else f"trace-{self._trace_counter}",
            span_id=f"span-{self._trace_counter}-{id(self)}",
            parent_id=parent.span_id if parent else None,
            start_time=time.time(),
        )
        self._spans.append(span)
        return span

    def end_span(self, span: Span) -> None:
        span.finish()
        if span in self._spans:
            self._spans.remove(span)
        self._finished.append(span)

    def trace_event(self, event: Event) -> Span:
        """Create a span from an event."""
        span = self.start_span(f"event:{event.event_type.value}")
        span.add_event(
            "event",
            {
                "event_type": event.event_type.value,
                "source": event.source,
                "topic": event.topic,
            },
        )
        return span

    def export(self) -> List[Dict[str, Any]]:
        """Export finished spans as dicts."""
        return [
            {
                "name": s.name,
                "trace_id": s.trace_id,
                "span_id": s.span_id,
                "parent_id": s.parent_id,
                "duration_ms": round((s.end_time or time.time()) - s.start_time, 3) * 1000,
                "attributes": s.attributes,
                "events": s.events,
            }
            for s in self._finished
        ]

    @contextmanager
    def span(self, name: str) -> Generator[Span, None, None]:
        s = self.start_span(name)
        try:
            yield s
        finally:
            self.end_span(s)

    @asynccontextmanager
    async def async_span(self, name: str) -> AsyncGenerator[Span, None]:
        """Async context manager for creating spans in async functions."""
        s = self.start_span(name)
        try:
            yield s
        finally:
            self.end_span(s)

    def get_finished_spans(self) -> List[Span]:
        """Return all finished spans."""
        return list(self._finished)

    def reset(self) -> None:
        """Clear all finished and in-progress spans."""
        self._spans.clear()
        self._finished.clear()
        self._trace_counter = 0
