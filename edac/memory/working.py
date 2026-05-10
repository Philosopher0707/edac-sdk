"""Working Memory — Scratchpad for a single step or reasoning trace.

Ephemeral, agent-scoped, cleared between steps. Used for intermediate
computations, scratch notes, and temporary state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WorkingMemory:
    """Per-agent scratchpad."""

    agent_id: str
    entries: List[Dict[str, Any]] = field(default_factory=list)
    max_entries: int = 100

    def write(self, key: str, value: Any) -> None:
        self.entries.append({"key": key, "value": value, "type": "write"})
        self._trim()

    def read(self, key: str) -> Optional[Any]:
        for entry in reversed(self.entries):
            if entry.get("key") == key and entry.get("type") == "write":
                return entry.get("value")
        return None

    def append(self, text: str) -> None:
        self.entries.append({"text": text, "type": "append"})
        self._trim()

    def get_log(self) -> List[str]:
        return [str(e) for e in self.entries]

    def clear(self) -> None:
        self.entries.clear()

    def _trim(self) -> None:
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries:]
