"""Prompt Injection Guardrails — Parallel screening processes."""

from __future__ import annotations

import logging
import re
from typing import List

logger = logging.getLogger("edac.security.guardrails")


class PromptGuardrail:
    """Simple regex-based guardrail for prompt injection detection."""

    def __init__(self) -> None:
        self._patterns: List[re.Pattern] = []
        self._add_default_patterns()

    def _add_default_patterns(self) -> None:
        patterns = [
            r"ignore previous instructions",
            r"disregard (all|your) (instructions|rules)",
            r"system prompt",
            r"you are now",
            r"DAN mode",
            r"jailbreak",
        ]
        for p in patterns:
            self._patterns.append(re.compile(p, re.IGNORECASE))

    def check(self, text: str) -> bool:
        """Return True if text passes guardrails (no injection detected)."""
        for pattern in self._patterns:
            if pattern.search(text):
                logger.warning(f"Guardrail triggered: {pattern.pattern}")
                return False
        return True

    def add_pattern(self, pattern: str) -> None:
        self._patterns.append(re.compile(pattern, re.IGNORECASE))
