"""Token Budget Tracker — Per-agent, per-session, per-plan limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class BudgetTracker:
    """Tracks token consumption against limits."""

    agent_limit: int = 128000
    session_limit: int = 512000
    plan_limit: int = 256000

    _agent_usage: Dict[str, int] = field(default_factory=dict)
    _session_usage: int = 0
    _plan_usage: int = 0

    def consume(self, agent_id: str, tokens: int) -> None:
        self._agent_usage[agent_id] = self._agent_usage.get(agent_id, 0) + tokens
        self._session_usage += tokens
        self._plan_usage += tokens

    def agent_usage(self, agent_id: str) -> int:
        return self._agent_usage.get(agent_id, 0)

    def agent_remaining(self, agent_id: str) -> int:
        return max(0, self.agent_limit - self.agent_usage(agent_id))

    def session_remaining(self) -> int:
        return max(0, self.session_limit - self._session_usage)

    def plan_remaining(self) -> int:
        return max(0, self.plan_limit - self._plan_usage)

    def is_over_budget(self, agent_id: str) -> bool:
        return (
            self.agent_usage(agent_id) >= self.agent_limit
            or self._session_usage >= self.session_limit
            or self._plan_usage >= self.plan_limit
        )

    def reset_agent(self, agent_id: str) -> None:
        self._session_usage -= self._agent_usage.pop(agent_id, 0)

    def reset_session(self) -> None:
        self._agent_usage.clear()
        self._session_usage = 0

    def reset_plan(self) -> None:
        self._plan_usage = 0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "agent_limit": self.agent_limit,
            "session_limit": self.session_limit,
            "plan_limit": self.plan_limit,
            "session_usage": self._session_usage,
            "plan_usage": self._plan_usage,
            "agent_usages": dict(self._agent_usage),
        }
