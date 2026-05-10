"""Model Router — Route subtasks to appropriate models.

Simple routing based on token budget and task complexity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ModelRoute:
    model: str
    reason: str


class ModelRouter:
    """Routes tasks to models based on cost/quality tradeoffs."""

    def __init__(
        self,
        cheap_model: str = "claude-haiku-4-5",
        default_model: str = "claude-sonnet-4-6",
        premium_model: str = "claude-opus-4-7",
        cheap_threshold: int = 4000,
    ):
        self.cheap_model = cheap_model
        self.default_model = default_model
        self.premium_model = premium_model
        self.cheap_threshold = cheap_threshold

    def route(self, prompt_tokens: int, complexity: str = "normal") -> ModelRoute:
        if complexity == "simple" or prompt_tokens < self.cheap_threshold:
            return ModelRoute(self.cheap_model, "low complexity / small prompt")
        if complexity == "hard":
            return ModelRoute(self.premium_model, "high complexity")
        return ModelRoute(self.default_model, "default")
