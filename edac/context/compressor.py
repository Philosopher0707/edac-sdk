"""Context Compression Strategies.

- Summarization of old context
- Selective retention
- Hierarchical compression
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Dict, List, Optional

from edac.memory.short_term import ShortTermMemory, WindowEntry

logger = logging.getLogger("edac.context.compressor")

# Common English stop words for topic extraction
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "must", "shall",
    "can", "need", "dare", "ought", "used", "to", "of", "in",
    "for", "on", "with", "at", "by", "from", "as", "into",
    "through", "during", "before", "after", "above", "below",
    "between", "under", "and", "but", "or", "yet", "so", "if",
    "because", "although", "though", "while", "where", "when",
    "that", "which", "who", "whom", "whose", "what", "this",
    "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "me", "him", "her", "us", "them", "my", "your", "his", "her",
    "its", "our", "their", "mine", "yours", "hers", "ours", "theirs",
    "myself", "yourself", "himself", "herself", "itself", "ourselves",
    "yourselves", "themselves", "am", "it", "entry", "hello", "hi",
})


class ContextCompressor:
    """Compress context windows by summarizing or dropping entries.

    Strategy:
        1. Preserve critical entries (original system prompts, user, human).
        2. Detect existing summary entries and merge their counts into the
           new summary — preventing unbounded accumulation of system msgs.
        3. Keep most recent droppable entries within the remaining budget.
        4. Summarize older droppable entries into a compact count + topics
           system message.
        5. If the summary itself is too long, truncate it token-aware.
    """

    def __init__(
        self,
        target_tokens: int = 4000,
        max_summary_ratio: float = 0.25,
    ) -> None:
        self.target_tokens = target_tokens
        self.max_summary_ratio = max_summary_ratio

    def compress(self, window: ShortTermMemory) -> ShortTermMemory:
        """Compress a window to target token count."""
        entries = window.get_window()
        total = sum(e.tokens for e in entries)
        if total <= self.target_tokens:
            return window

        # Phase 1 — Separate critical, existing summaries, and droppable entries
        critical: List[WindowEntry] = []
        existing_summaries: List[WindowEntry] = []
        droppable: List[WindowEntry] = []

        for e in entries:
            if e.role in ("system", "user", "human"):
                if e.role == "system" and e.content.startswith("[Earlier context summarized]"):
                    existing_summaries.append(e)
                else:
                    critical.append(e)
            else:
                droppable.append(e)

        # Parse previously summarized entry counts from existing summaries
        previously_summarized: Dict[str, int] = {}
        for summary in existing_summaries:
            counts = self._parse_summary_counts(summary.content)
            for role, count in counts.items():
                previously_summarized[role] = previously_summarized.get(role, 0) + count

        keep_tokens = sum(e.tokens for e in critical)

        # Phase 2 — Reserve budget for summary; keep recent droppable entries
        max_summary_tokens = int(self.target_tokens * self.max_summary_ratio)
        budget_for_recent = self.target_tokens - keep_tokens - max_summary_tokens

        recent: List[WindowEntry] = []
        if budget_for_recent > 0:
            for e in reversed(droppable):
                if e.tokens <= budget_for_recent:
                    recent.insert(0, e)  # maintain chronological order
                    budget_for_recent -= e.tokens
                else:
                    break

        to_summarize = droppable[: len(droppable) - len(recent)]

        # Phase 3 — Generate compact semantic summary
        summary_text = self._summarize(to_summarize, previously_summarized)
        if summary_text:
            full_summary = f"[Earlier context summarized] {summary_text}"
            summary_tokens = window._estimate_tokens(full_summary)

            # Hard cap: summary must not push total over target
            available = self.target_tokens - keep_tokens - sum(e.tokens for e in recent)
            if available <= 0:
                # No room — create the tiniest possible summary
                summary_text = self._minimal_summary(to_summarize, previously_summarized)
                full_summary = f"[Earlier context summarized] {summary_text}"
                summary_tokens = window._estimate_tokens(full_summary)
            elif summary_tokens > available:
                summary_text = self._truncate_to_tokens(summary_text, available)
                full_summary = f"[Earlier context summarized] {summary_text}"
                summary_tokens = window._estimate_tokens(full_summary)
            elif summary_tokens > max_summary_tokens:
                summary_text = self._truncate_to_tokens(summary_text, max_summary_tokens)
                full_summary = f"[Earlier context summarized] {summary_text}"
                summary_tokens = window._estimate_tokens(full_summary)

            critical.insert(
                0,
                WindowEntry(
                    role="system",
                    content=full_summary,
                    tokens=summary_tokens,
                ),
            )

        # Phase 4 — Assemble new window without per-add budget enforcement
        new_window = ShortTermMemory(max_tokens=window.max_tokens)
        new_window._entries = critical + recent
        return new_window

    # ── Summarization internals ──

    def _summarize(
        self,
        entries: List[WindowEntry],
        previously_summarized: Optional[Dict[str, int]] = None,
    ) -> str:
        """Produce a compact semantic summary of dropped entries."""
        if not entries and not previously_summarized:
            return ""

        # Count by role (including previously summarized counts)
        counts: Dict[str, int] = dict(previously_summarized) if previously_summarized else {}
        for e in entries:
            counts[e.role] = counts.get(e.role, 0) + 1

        parts = [f"{count} {role}" for role, count in sorted(counts.items())]
        summary = ", ".join(parts)

        # Add topics if there's room (~15 tokens or less)
        all_texts = [e.content for e in entries]
        topics = self._extract_topics(all_texts)
        if topics:
            topics_str = ", ".join(topics[:3])
            candidate = f"{summary} ({topics_str})"
            if len(candidate) <= 60:
                summary = candidate

        return summary

    def _minimal_summary(
        self,
        entries: List[WindowEntry],
        previously_summarized: Optional[Dict[str, int]] = None,
    ) -> str:
        """Ultra-compact summary when budget is extremely tight."""
        counts: Dict[str, int] = dict(previously_summarized) if previously_summarized else {}
        for e in entries:
            counts[e.role] = counts.get(e.role, 0) + 1
        total = sum(counts.values())
        if total == 0:
            return ""
        roles = ", ".join(sorted(counts.keys()))
        return f"{total} {roles} msgs"

    @staticmethod
    def _extract_topics(texts: List[str]) -> List[str]:
        """Extract dominant topics from a list of texts via simple keyword frequency."""
        all_text = " ".join(texts).lower()
        words = re.findall(r"\b[a-z][a-z]{2,}\b", all_text)
        filtered = (w for w in words if w not in _STOP_WORDS)
        counter: Counter[str] = Counter(filtered)
        return [w for w, _ in counter.most_common(10)]

    @staticmethod
    def _parse_summary_counts(text: str) -> Dict[str, int]:
        """Extract role counts from summary strings.

        Handles both compact format ("3 assistant (topics)") and legacy
        format ("assistant: content | assistant: content").
        """
        counts: Dict[str, int] = {}
        text = text.replace("[Earlier context summarized] ", "")

        # Compact format: "3 assistant (topics)"
        for match in re.finditer(
            r"(\d+)\s+(assistant|user|system|human|tool|event)\b", text
        ):
            role = match.group(2)
            counts[role] = counts.get(role, 0) + int(match.group(1))

        # Legacy format fallback: count "role: content" segments
        if not counts:
            for match in re.finditer(
                r"(assistant|user|system|human|tool|event)\s*:\s*", text
            ):
                role = match.group(1)
                counts[role] = counts.get(role, 0) + 1

        return counts

    @staticmethod
    def _truncate_to_tokens(text: str, max_tokens: int) -> str:
        """Truncate text to fit within max_tokens (roughly 4 chars / token)."""
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        cutoff = max_chars - 3
        if cutoff <= 0:
            return text[: max(1, max_chars)]
        truncated = text[:cutoff]
        # Try to break at a word boundary
        last_space = truncated.rfind(" ")
        if last_space > cutoff * 0.5:
            truncated = truncated[:last_space]
        return truncated + "..."
