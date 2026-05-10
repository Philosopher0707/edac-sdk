"""Short-Term Memory — Session-scoped context window.

Maintains a rolling window of recent events and messages.
When the window approaches the token budget, older content is summarized
or dropped. This is the agent's "working memory" across steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("edac.memory.short_term")


@dataclass
class WindowEntry:
    """A single entry in the context window."""
    role: str  # "system", "user", "assistant", "tool", "event"
    content: str
    tokens: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class ShortTermMemory:
    """Rolling context window with token budget enforcement."""

    def __init__(self, max_tokens: int = 8000, reserved_tokens: int = 1000):
        self.max_tokens = max_tokens
        self.reserved_tokens = min(reserved_tokens, max_tokens // 2)
        self._entries: List[WindowEntry] = []

    def add(self, entry: WindowEntry) -> None:
        self._entries.append(entry)
        self._enforce_budget()

    def add_text(self, role: str, text: str, tokens: int = 0) -> None:
        self.add(WindowEntry(role=role, content=text, tokens=tokens or self._estimate_tokens(text)))

    def get_window(self) -> List[WindowEntry]:
        return list(self._entries)

    def get_text(self) -> str:
        return "\n".join(f"[{e.role}] {e.content}" for e in self._entries)

    def total_tokens(self) -> int:
        return sum(e.tokens for e in self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def _enforce_budget(self) -> None:
        budget = self.max_tokens - self.reserved_tokens
        while self.total_tokens() > budget and self._entries:
            removed = self._entries.pop(0)
            logger.debug(f"Dropped from context window: {removed.role} ({removed.tokens} tokens)")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        # Rough estimate: ~4 chars per token
        return max(1, len(text) // 4)
