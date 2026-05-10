"""Context Compression Strategies.

- Summarization of old context
- Selective retention
- Hierarchical compression
"""

from __future__ import annotations

from typing import List

from edac.memory.short_term import ShortTermMemory, WindowEntry


class ContextCompressor:
    """Compress context windows by summarizing or dropping entries."""

    def __init__(self, target_tokens: int = 4000) -> None:
        self.target_tokens = target_tokens

    def compress(self, window: ShortTermMemory) -> ShortTermMemory:
        """Compress a window to target token count."""
        entries = window.get_window()
        total = sum(e.tokens for e in entries)
        if total <= self.target_tokens:
            return window

        # Strategy: drop oldest entries first, keep system and recent
        keep: List[WindowEntry] = []
        dropped: List[WindowEntry] = []
        for e in entries:
            if e.role == "system" or e.role == "human":
                keep.append(e)
            else:
                dropped.append(e)

        # If still over budget, summarize dropped entries
        summary = self._summarize(dropped)
        if summary:
            keep.insert(0, WindowEntry(role="system", content=f"[Earlier context summarized] {summary}", tokens=window._estimate_tokens(summary)))

        new_window = ShortTermMemory(max_tokens=window.max_tokens)
        for e in keep:
            new_window.add(e)
        return new_window

    def _summarize(self, entries: List[WindowEntry]) -> str:
        if not entries:
            return ""
        texts = [f"{e.role}: {e.content[:200]}" for e in entries]
        return " | ".join(texts)
