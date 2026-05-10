"""Agent Builder — Fluent builder for agent configuration.

Usage:
    agent = (
        AgentBuilder()
        .name("coder")
        .model("claude-sonnet-4-6")
        .skill("python-refactor")
        .sandbox(True)
        .build()
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from edac.agent.lifecycle import AgentConfig


@dataclass
class AgentBuilder:
    """Fluent builder for agent configuration."""

    _name: str = "agent"
    _type: str = "generic"
    _model: Optional[str] = None
    _skills: List[str] = field(default_factory=list)
    _goal: Optional[str] = None
    _sandbox: bool = False
    _max_restarts: int = 3
    _config: Dict[str, Any] = field(default_factory=dict)

    def name(self, value: str) -> AgentBuilder:
        self._name = value
        return self

    def type(self, value: str) -> AgentBuilder:
        self._type = value
        return self

    def model(self, value: str) -> AgentBuilder:
        self._model = value
        return self

    def skill(self, value: str) -> AgentBuilder:
        self._skills.append(value)
        return self

    def skills(self, values: List[str]) -> AgentBuilder:
        self._skills.extend(values)
        return self

    def goal(self, value: str) -> AgentBuilder:
        self._goal = value
        return self

    def sandbox(self, enabled: bool = True) -> AgentBuilder:
        self._sandbox = enabled
        return self

    def max_restarts(self, value: int) -> AgentBuilder:
        self._max_restarts = value
        return self

    def config(self, **kwargs: Any) -> AgentBuilder:
        self._config.update(kwargs)
        return self

    def build(self) -> AgentConfig:
        return AgentConfig(
            name=self._name,
            agent_type=self._type,
            model=self._model,
            skills=self._skills,
            goal=self._goal,
            max_restarts=self._max_restarts,
            config={"sandbox": self._sandbox, **self._config},
        )
