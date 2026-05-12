"""Long-Term Memory — Persistent cross-session storage.

Stores facts, patterns, and preferences across sessions.
This is a simple in-memory vector store for now.
Production: use ChromaDB, Pinecone, pgvector, or sqlite-vec.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("edac.memory.long_term")


@dataclass
class MemoryEntry:
    """A single long-term memory entry."""

    id: str
    content: str
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"


class LongTermMemory:
    """In-memory long-term memory with cosine-similarity retrieval."""

    def __init__(self) -> None:
        self._entries: Dict[str, MemoryEntry] = {}

    def store(
        self,
        content: str,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "unknown",
    ) -> str:
        entry_id = hashlib.sha256(content.encode()).hexdigest()[:16]
        self._entries[entry_id] = MemoryEntry(
            id=entry_id,
            content=content,
            embedding=embedding,
            metadata=metadata or {},
            source=source,
        )
        return entry_id

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        return self._entries.get(entry_id)

    def search(self, query_embedding: List[float], top_k: int = 5) -> List[MemoryEntry]:
        """Find nearest neighbors by cosine similarity."""
        scored = []
        for entry in self._entries.values():
            if entry.embedding is None:
                continue
            sim = self._cosine_sim(query_embedding, entry.embedding)
            scored.append((sim, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    def search_text(self, query: str, top_k: int = 5) -> List[MemoryEntry]:
        """Simple substring search when embeddings not available."""
        q = query.lower()
        scored = []
        for entry in self._entries.values():
            score = entry.content.lower().count(q)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    def delete(self, entry_id: str) -> bool:
        return self._entries.pop(entry_id, None) is not None

    @staticmethod
    def _cosine_sim(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
