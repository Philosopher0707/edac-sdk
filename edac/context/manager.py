"""Context Manager — Token budget tracking and context lifecycle.

Tracks token usage per agent, session, and plan.
Routes to cheaper models for simple subtasks.
Coordinates with ShortTermMemory for window management.
Manages WorkingMemory (per-step scratchpad), LongTermMemory (facts),
and EpisodicMemory (event trajectory).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from edac.event.schema import Event
from edac.memory.episodic import EpisodicMemory
from edac.memory.long_term import LongTermMemory
from edac.memory.short_term import ShortTermMemory, WindowEntry
from edac.memory.working import WorkingMemory
from edac.context.budget import BudgetTracker
from edac.context.compressor import ContextCompressor
from edac.model import ChatMessage, ModelRegistry

logger = logging.getLogger("edac.context.manager")


@dataclass
class ContextConfig:
    """Configuration for context management."""
    max_tokens_per_agent: int = 128000
    max_tokens_per_session: int = 512000
    max_tokens_per_plan: int = 256000
    cheap_model_threshold: int = 4000
    compression_enabled: bool = True
    compression_trigger_tokens: int = 64000
    cheap_model: str = "claude-haiku-4-5"
    default_model: str = "claude-sonnet-4-6"
    premium_model: str = "claude-opus-4-7"
    default_provider: str = "ollama"
    # memory persistence
    episodic_path: Optional[Path] = None
    enable_augmentation: bool = True


class ContextManager:
    """Manages context windows, token budgets, and model routing."""

    def __init__(
        self,
        config: Optional[ContextConfig] = None,
        registry: Optional[ModelRegistry] = None,
        metrics: Optional[Any] = None,
    ):
        self.config = config or ContextConfig()
        self.registry = registry or ModelRegistry()
        self.metrics = metrics
        self._budget = BudgetTracker(
            agent_limit=self.config.max_tokens_per_agent,
            session_limit=self.config.max_tokens_per_session,
            plan_limit=self.config.max_tokens_per_plan,
        )
        self._windows: Dict[str, ShortTermMemory] = {}
        self._working: Dict[str, WorkingMemory] = {}
        self._long_term = LongTermMemory()
        self._episodic = EpisodicMemory(
            path=self.config.episodic_path,
        )
        self._compressor: Optional[ContextCompressor] = None
        if self.config.compression_enabled:
            self._compressor = ContextCompressor(
                target_tokens=self.config.compression_trigger_tokens,
            )

    # ── Short-Term Memory ──

    def get_window(self, agent_id: str) -> ShortTermMemory:
        if agent_id not in self._windows:
            self._windows[agent_id] = ShortTermMemory(
                max_tokens=self.config.max_tokens_per_agent,
            )
        return self._windows[agent_id]

    def add_to_window(self, agent_id: str, role: str, text: str, tokens: int = 0) -> None:
        window = self.get_window(agent_id)
        window.add_text(role, text, tokens=tokens)
        estimated = tokens or window._estimate_tokens(text)
        self._budget.consume(agent_id, estimated)
        # Invoke compressor when raw entries exceed trigger
        if self._compressor is not None:
            if window.total_tokens() > self.config.compression_trigger_tokens:
                logger.debug(
                    "Compressing context window for %s: %s tokens > %s trigger",
                    agent_id,
                    window.total_tokens(),
                    self.config.compression_trigger_tokens,
                )
                self._windows[agent_id] = self._compressor.compress(window)

    def get_context(self, agent_id: str) -> str:
        return self.get_window(agent_id).get_text()

    def clear_window(self, agent_id: str) -> None:
        self._windows.pop(agent_id, None)

    # ── Working Memory ──

    def get_working(self, agent_id: str) -> WorkingMemory:
        if agent_id not in self._working:
            self._working[agent_id] = WorkingMemory(agent_id=agent_id)
        return self._working[agent_id]

    # ── Long-Term Memory ──

    def get_long_term(self) -> LongTermMemory:
        return self._long_term

    def promote_to_long_term(
        self,
        agent_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Move select window entries into long-term memory and return count stored."""
        window = self.get_window(agent_id)
        stored = 0
        for entry in window.get_window():
            # Skip assistant-generated content (generated, not factual)
            if entry.role in ("system", "assistant"):
                continue
            content = f"[{entry.role}] {entry.content}"
            self._long_term.store(
                content=content,
                metadata={
                    "agent_id": agent_id,
                    "role": entry.role,
                    **(entry.metadata or {}),
                    **(metadata or {}),
                },
                source=entry.role,
            )
            stored += 1
        return stored

    def store_long_term(
        self,
        content: str,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "api",
    ) -> str:
        return self._long_term.store(
            content=content,
            embedding=embedding,
            metadata=metadata,
            source=source,
        )

    # ── Episodic Memory ──

    def get_episodic(self) -> EpisodicMemory:
        return self._episodic

    def record_episode(self, event: Event) -> None:
        """Append an event to the episodic log."""
        self._episodic.append(event)

    # ── Context augmentation for LLM calls ──

    def _augment_prompt(self, agent_id: str, prompt: str) -> str:
        """Injects relevant long-term facts and episodic snippets into the prompt."""
        parts: List[str] = []
        # Retrieve matching long-term memories via text search
        memories = self._long_term.search_text(prompt, top_k=3)
        if memories:
            parts.append("--- Retrieved Context ---")
            for m in memories:
                parts.append(f"- {m.content}")
            parts.append("--- End Retrieved Context ---\n")
        # Retrieve recent episodic notes (no embedding yet)
        recent = self._episodic.get_by_type("system.log")
        if recent and len(recent) > 0:
            parts.append("--- Recent Events ---")
            for e in recent[-3:]:
                parts.append(f"- {e.source}: {str(e.payload)[:200]}")
            parts.append("--- End Recent Events ---\n")
        parts.append(prompt)
        return "\n".join(parts)

    def clear(self, agent_id: str) -> None:
        self._windows.pop(agent_id, None)
        self._working.pop(agent_id, None)
        self._budget.reset_agent(agent_id)

    def select_model(self, agent_id: str, task_complexity: str = "normal") -> str:
        """Route to cheaper models when appropriate."""
        used = self._budget.agent_usage(agent_id)
        if task_complexity == "simple" or used < self.config.cheap_model_threshold:
            return self.config.cheap_model
        if task_complexity == "hard":
            return self.config.premium_model
        return self.config.default_model

    async def chat(
        self,
        agent_id: str,
        prompt: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Send a chat request to the configured provider."""
        prov_name = provider or self.config.default_provider
        prov = self.registry.get(prov_name)
        if prov is None:
            available = await self.registry.get_available()
            raise ValueError(f"Provider '{prov_name}' not found. Available: {available}")

        messages: List[ChatMessage] = []
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))

        # Add conversation history from window
        window = self.get_window(agent_id)
        for entry in window.get_window():
            # Preserve known roles; default unknown ones to assistant
            if entry.role in ("system", "user", "assistant", "tool"):
                chat_role = entry.role
            else:
                chat_role = "assistant"
            messages.append(ChatMessage(role=chat_role, content=entry.content))

        # Augment prompt with long-term + episodic context before injection
        if self.config.enable_augmentation:
            prompt = self._augment_prompt(agent_id, prompt)

        messages.append(ChatMessage(role="user", content=prompt))

        model = model or self.select_model(agent_id)
        completion = await prov.chat(messages, model=model, **kwargs)

        # Track token usage
        usage = completion.usage
        if isinstance(usage, dict):
            total = usage.get("total_tokens", 0)
        else:
            total = usage or 0
        self._budget.consume(agent_id, total)
        if self.metrics:
            self.metrics.counter("llm_tokens_total", labels={"provider": prov_name, "model": model or "unknown"}).inc(total)

        # Store response in window
        self.add_to_window(agent_id, "assistant", completion.content, total)

        return completion.content

    def get_stats(self) -> Dict[str, Any]:
        return {
            "budget": self._budget.snapshot(),
            "active_windows": len(self._windows),
            "active_working": len(self._working),
            "long_term_entries": len(self._long_term._entries),
            "episodic_entries": len(self._episodic._events),
            "providers": self.registry.list_providers(),
        }
