"""Episodic Memory — Immutable event log for full trajectory replay.

Every event in the system is an episode. This module provides:
- Append-only event storage
- Trajectory retrieval by correlation_id
- Replay capability (re-emit events for debugging)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from edac.event.schema import Event

logger = logging.getLogger("edac.memory.episodic")


class EpisodicMemory:
    """Append-only episodic store backed by JSON lines file."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self._events: List[Event] = []
        if path:
            self._load()

    def append(self, event: Event) -> None:
        self._events.append(event)
        if self.path:
            self._append_to_disk(event)

    def get_trajectory(self, correlation_id: UUID) -> List[Event]:
        return [e for e in self._events if e.correlation_id == correlation_id]

    def get_by_type(self, event_type: str) -> List[Event]:
        return [e for e in self._events if e.event_type.value == event_type]

    def replay(self, correlation_id: UUID) -> List[Event]:
        """Return events in chronological order for replay."""
        events = self.get_trajectory(correlation_id)
        return sorted(events, key=lambda e: e.timestamp)

    def export(self, correlation_id: UUID, output_path: Path) -> None:
        events = self.replay(correlation_id)
        output_path.write_text(
            "\n".join(e.model_dump_json() for e in events),
            encoding="utf-8",
        )
        logger.info(f"Exported {len(events)} events to {output_path}")

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                self._events.append(Event.model_validate_json(line))
            except Exception as e:
                logger.warning(f"Skipping corrupt event line: {e}")

    def _append_to_disk(self, event: Event) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
