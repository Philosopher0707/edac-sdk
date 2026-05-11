"""Approval Gates — Require human approval for destructive/irreversible actions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
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
        self._event = asyncio.Event()
        # If already approved (via pre-seeding), set immediately
        if self.is_approved():
            self._event.set()

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
        if self.is_approved():
            self._event.set()
        return len(self.approvals) >= self.required_approvers

    def is_approved(self) -> bool:
        return len(self.approvals) >= self.required_approvers

    async def wait_for_approval(self, timeout: Optional[float] = None) -> bool:
        """Block until the gate is approved or the timeout elapses.

        Returns ``True`` if approved, ``False`` on timeout.
        """
        if self.is_approved():
            return True
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def reset(self) -> None:
        """Reset the gate so it blocks again (useful for one-shot gates)."""
        self._event.clear()


class ApprovalManager:
    """Manages a set of approval gates."""

    def __init__(self) -> None:
        self._gates: List[ApprovalGate] = []

    def add_gate(self, gate: ApprovalGate) -> None:
        self._gates.append(gate)

    def check(self, action: str) -> Optional[ApprovalGate]:
        """Return the first triggered, unapproved gate, or None if no gate applies."""
        for gate in self._gates:
            if gate.is_triggered(action):
                if not gate.is_approved():
                    return gate
        return None

    def approve(self, action: str, approver: str) -> bool:
        """Approve all gates that match *action*."""
        approved_any = False
        for gate in self._gates:
            if gate.is_triggered(action):
                approved_any |= gate.approve(approver)
        return approved_any

    @property
    def gates(self) -> List[ApprovalGate]:
        return list(self._gates)
