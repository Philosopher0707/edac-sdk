"""Context Manager — Token budget tracking and context lifecycle.

Tracks token usage per agent, session, and plan.
Routes to cheaper models for simple subtasks.
Coordinates with ShortTermMemory for window management.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from edac.memory.short_term import ShortTermMemory, WindowEntry
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


class ContextManager:
    """Manages context windows, token budgets, and model routing."""

    def __init__(self, config: Optional[ContextConfig] = None, registry: Optional[ModelRegistry] = None, metrics: Optional[Any] = None):
        self.config = config or ContextConfig()
        self.registry = registry or ModelRegistry()
        self.metrics = metrics
        self._budget = BudgetTracker(
            agent_limit=self.config.max_tokens_per_agent,
            session_limit=self.config.max_tokens_per_session,
            plan_limit=self.config.max_tokens_per_plan,
        )
        self._windows: Dict[str, ShortTermMemory] = {}
        self._compressor: Optional[ContextCompressor] = None
        if self.config.compression_enabled:
            self._compressor = ContextCompressor(
                target_tokens=self.config.compression_trigger_tokens,
            )

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

    def clear(self, agent_id: str) -> None:
        self._windows.pop(agent_id, None)
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
            "providers": self.registry.list_providers(),
        }
