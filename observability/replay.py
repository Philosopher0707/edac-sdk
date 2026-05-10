"""Trajectory Export — Export event logs for replay, debugging, evaluation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from edac.event.schema import Event
from edac.memory.episodic import EpisodicMemory

logger = logging.getLogger("edac.observability.replay")


class TrajectoryExporter:
    """Export full event trajectories for replay or evaluation."""

    def __init__(self, memory: EpisodicMemory) -> None:
        self.memory = memory

    def export(
        self,
        correlation_id: UUID,
        output_path: Path,
        format: str = "jsonl",
    ) -> Path:
        events = self.memory.replay(correlation_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "jsonl":
            output_path.write_text(
                "\n".join(e.model_dump_json() for e in events) + "\n",
                encoding="utf-8",
            )
        elif format == "json":
            output_path.write_text(
                json.dumps([json.loads(e.model_dump_json()) for e in events], indent=2),
                encoding="utf-8",
            )
        else:
            raise ValueError(f"Unknown format: {format}")

        logger.info(f"Exported {len(events)} events to {output_path}")
        return output_path

    def summary(self, correlation_id: UUID) -> Dict[str, Any]:
        events = self.memory.replay(correlation_id)
        if not events:
            return {"correlation_id": str(correlation_id), "event_count": 0}

        by_type: Dict[str, int] = {}
        for e in events:
            by_type[e.event_type.value] = by_type.get(e.event_type.value, 0) + 1

        return {
            "correlation_id": str(correlation_id),
            "event_count": len(events),
            "start_time": events[0].timestamp.isoformat(),
            "end_time": events[-1].timestamp.isoformat(),
            "event_types": by_type,
        }
