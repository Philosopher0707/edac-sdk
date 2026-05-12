"""Secrets Manager — Scoped, rotated, budget-limited credentials.

Production implementations use HashiCorp Vault, AWS Secrets Manager,
or 1Password. This module provides the interface and a fallback
in-memory / env-backed store.
"""

from __future__ import annotations

import logging
import os
import secrets as _secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("edac.security.secrets")


@dataclass
class Secret:
    """A scoped secret with metadata."""

    key: str
    value: str
    scope: str = "global"  # agent, task, global
    budget: Optional[int] = None  # max calls before rotation
    used: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def consume(self) -> None:
        self.used += 1
        if self.budget is not None and self.used >= self.budget:
            logger.warning(f"Secret {self.key} budget exhausted ({self.used}/{self.budget})")


class SecretsManager:
    """Scoped secret storage with rotation support."""

    def __init__(self) -> None:
        self._secrets: Dict[str, Secret] = {}

    # ── Core API ──

    def set(
        self, key: str, value: str, scope: str = "global", budget: Optional[int] = None
    ) -> None:
        """Store a secret."""
        self._secrets[key] = Secret(
            key=key,
            value=value,
            scope=scope,
            budget=budget,
        )

    def get(self, key: str, scope: Optional[str] = None) -> Optional[str]:
        """Retrieve a secret value. Scope must match if provided."""
        s = self._secrets.get(key)
        if s is None:
            return None
        if scope is not None and s.scope != scope:
            logger.warning(f"Secret {key} scope mismatch: expected {scope}, got {s.scope}")
            return None
        s.consume()
        return s.value

    def rotate(self, key: str, new_value: Optional[str] = None) -> str:
        """Rotate a secret. Generates new value if not provided."""
        s = self._secrets.get(key)
        if s is None:
            raise KeyError(f"Secret {key} not found")
        new_val = new_value or self._generate()
        s.value = new_val
        s.used = 0
        logger.info(f"Rotated secret {key} (scope={s.scope})")
        return new_val

    def delete(self, key: str) -> bool:
        """Remove a secret."""
        existed = key in self._secrets
        self._secrets.pop(key, None)
        return existed

    # ── Scoped helpers ──

    def set_for_agent(
        self, agent_id: str, key: str, value: str, budget: Optional[int] = None
    ) -> None:
        """Store a secret scoped to an agent."""
        self.set(key, value, scope=f"agent:{agent_id}", budget=budget)

    def get_for_agent(self, agent_id: str, key: str) -> Optional[str]:
        """Retrieve a secret scoped to an agent."""
        return self.get(key, scope=f"agent:{agent_id}")

    def set_for_task(
        self, task_id: str, key: str, value: str, budget: Optional[int] = None
    ) -> None:
        """Store a secret scoped to a task."""
        self.set(key, value, scope=f"task:{task_id}", budget=budget)

    def get_for_task(self, task_id: str, key: str) -> Optional[str]:
        """Retrieve a secret scoped to a task."""
        return self.get(key, scope=f"task:{task_id}")

    def load_env(self, prefix: str = "EDAC_SECRET_") -> int:
        """Load secrets from environment variables matching prefix."""
        count = 0
        for env_key, value in os.environ.items():
            if env_key.startswith(prefix):
                key = env_key[len(prefix) :]
                self.set(key, value, scope="env")
                count += 1
        logger.info(f"Loaded {count} secrets from environment")
        return count

    # ── Introspection ──

    def list_keys(self, scope: Optional[str] = None) -> List[str]:
        """List all secret keys, optionally filtered by scope."""
        if scope is None:
            return list(self._secrets.keys())
        return [k for k, s in self._secrets.items() if s.scope == scope]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total": len(self._secrets),
            "scopes": list({s.scope for s in self._secrets.values()}),
        }

    # ── Internal ──

    @staticmethod
    def _generate() -> str:
        return _secrets.token_urlsafe(32)
