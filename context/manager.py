"""Context Manager — Token budget tracking and context lifecycle.

Tracks token usage per agent, session, and plan.
Routes to cheaper models for simple subtasks.
Coordinates with ShortTermMemory for window management.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from edac.memory.short_term import ShortTermMemory
from edac.context.budget import BudgetTracker

logger = logging.getLogger("edac.context.manager")


@dataclass
class ContextConfig:
    """Configuration for context management."""
    max_tokens_per_agent: int = 128000
    max_tokens_per_session: int = 512000
    max_tokens_per_plan: int = 256000
    cheap_model_threshold: int = 4000
    cheap_model: str = "claude-haiku-4-5"
    default_model: str = "claude-sonnet-4-6"
    premium_model: str = "claude-opus-4-7"


class ContextManager:
    """Manages context windows, token budgets, and model routing."""

    def __init__(self, config: Optional[ContextConfig] = None):
        self.config = config or ContextConfig()
        self._budget = BudgetTracker(
            agent_limit=self.config.max_tokens_per_agent,
            session_limit=self.config.max_tokens_per_session,
            plan_limit=self.config.max_tokens_per_plan,
        )
        self._windows: Dict[str, ShortTermMemory] = {}

    def get_window(self, agent_id: str) -> ShortTermMemory:
        if agent_id not in self._windows:
            self._windows[agent_id] = ShortTermMemory(
                max_tokens=self.config.max_tokens_per_agent,
            )
        return self._windows[agent_id]

    def add_to_window(self, agent_id: str, role: str, text: str, tokens: int = 0) -> None:
        window = self.get_window(agent_id)
        window.add_text(role, text, tokens)
        self._budget.consume(agent_id, tokens or window._estimate_tokens(text))

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

    def get_stats(self) -> Dict[str, Any]:
        return {
            "budget": self._budget.snapshot(),
            "active_windows": len(self._windows),
        }
