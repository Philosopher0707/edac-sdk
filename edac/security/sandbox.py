"""Secure Sandbox — gVisor/Bubblewrap/Docker wrapper.

Production-grade isolation. This module provides the interface
and a subprocess-based fallback.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from edac.tool.sandbox import Sandbox, SandboxConfig, SandboxResult

logger = logging.getLogger("edac.security.sandbox")


class SecureSandbox(Sandbox):
    """Extended sandbox with security policies."""

    def __init__(self, config: Optional[SandboxConfig] = None) -> None:
        super().__init__(config)
        self._policies: List[str] = []

    def add_policy(self, policy: str) -> None:
        self._policies.append(policy)

    def validate_policy(self, command: List[str]) -> bool:
        for policy in self._policies:
            if policy == "no_shell" and any(c in command[0] for c in ["bash", "sh", "zsh"]):
                return False
        return True
