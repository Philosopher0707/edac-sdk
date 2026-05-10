"""Approval Gates — Require human approval for destructive/irreversible actions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger("edac.human.approval")


@dataclass
class ApprovalGate:
    """An approval gate that pauses execution until approved."""
    trigger_on: str  # e.g. "tool.git.push"
    prompt: str
    timeout_seconds: float = 300.0
    required_approvers: int = 1
    approvals: List[str] = None

    def __post_init__(self):
        if self.approvals is None:
            self.approvals = []

    def is_triggered(self, action: str) -> bool:
        if self.trigger_on == "*":
            return True
        if self.trigger_on.endswith(".*"):
            prefix = self.trigger_on[:-1]
            return action.startswith(prefix)
        return action == self.trigger_on

    def approve(self, approver: str) -> bool:
        if approver not in self.approvals:
            self.approvals.append(approver)
        return len(self.approvals) >= self.required_approvers

    def is_approved(self) -> bool:
        return len(self.approvals) >= self.required_approvers


class ApprovalManager:
    """Manages a set of approval gates."""

    def __init__(self) -> None:
        self._gates: List[ApprovalGate] = []

    def add_gate(self, gate: ApprovalGate) -> None:
        self._gates.append(gate)

    def check(self, action: str) -> Optional[ApprovalGate]:
        """Return the first triggered gate, or None if no gate applies."""
        for gate in self._gates:
            if gate.is_triggered(action):
                if not gate.is_approved():
                    return gate
        return None

    def approve(self, action: str, approver: str) -> bool:
        for gate in self._gates:
            if gate.is_triggered(action):
                return gate.approve(approver)
        return False

    @property
    def gates(self) -> List[ApprovalGate]:
        return list(self._gates)
