"""Agent Registry — Service discovery, agent cards, capability advertisement.

The registry maintains a catalog of all agents in the system.
Other agents and tools can query it to discover capabilities and
route tasks to the right agent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from edac.agent.lifecycle import AgentInstance

logger = logging.getLogger("edac.agent.registry")


# ──────────────────────────────────────────────────────────────
# Agent Card (A2A-compatible)
# ──────────────────────────────────────────────────────────────

@dataclass
class AgentCard:
    """A2A-compatible agent metadata card.

    See: https://github.com/google/A2A
    """
    name: str
    description: str
    version: str = "1.0"
    capabilities: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    model: Optional[str] = None
    endpoint: Optional[str] = None  # URL for A2A communication
    input_modes: List[str] = field(default_factory=lambda: ["text"])
    output_modes: List[str] = field(default_factory=lambda: ["text"])
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "capabilities": self.capabilities,
            "skills": self.skills,
            "model": self.model,
            "endpoint": self.endpoint,
            "input_modes": self.input_modes,
            "output_modes": self.output_modes,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_agent(cls, agent: AgentInstance, description: str = "") -> AgentCard:
        return cls(
            name=agent.config.name,
            description=description or f"Agent {agent.config.name} ({agent.config.agent_type})",
            skills=agent.config.skills,
            model=agent.config.model,
            capabilities=[agent.config.agent_type],
        )


# ──────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────

class AgentRegistry:
    """Central registry for agent discovery and capability lookup."""

    def __init__(self) -> None:
        self._agents: Dict[str, AgentInstance] = {}
        self._cards: Dict[str, AgentCard] = {}
        self._by_capability: Dict[str, Set[str]] = {}
        self._by_skill: Dict[str, Set[str]] = {}

    def register(
        self,
        agent: AgentInstance,
        card: Optional[AgentCard] = None,
    ) -> None:
        self._agents[agent.agent_id] = agent
        self._cards[agent.agent_id] = card or AgentCard.from_agent(agent)

        # Index capabilities
        for cap in self._cards[agent.agent_id].capabilities:
            self._by_capability.setdefault(cap, set()).add(agent.agent_id)

        # Index skills
        for skill in self._cards[agent.agent_id].skills:
            self._by_skill.setdefault(skill, set()).add(agent.agent_id)

        logger.info(f"Registered agent {agent.agent_id} ({agent.config.name})")

    def unregister(self, agent_id: str) -> Optional[AgentInstance]:
        agent = self._agents.pop(agent_id, None)
        card = self._cards.pop(agent_id, None)

        if card:
            for cap in card.capabilities:
                self._by_capability.get(cap, set()).discard(agent_id)
            for skill in card.skills:
                self._by_skill.get(skill, set()).discard(agent_id)

        if agent:
            logger.info(f"Unregistered agent {agent_id}")
        return agent

    def get(self, agent_id: str) -> Optional[AgentInstance]:
        return self._agents.get(agent_id)

    def get_card(self, agent_id: str) -> Optional[AgentCard]:
        return self._cards.get(agent_id)

    def list_agents(self) -> List[AgentInstance]:
        return list(self._agents.values())

    def list_cards(self) -> List[AgentCard]:
        return list(self._cards.values())

    def find_by_capability(self, capability: str) -> List[AgentInstance]:
        return [self._agents[aid] for aid in self._by_capability.get(capability, set())]

    def find_by_skill(self, skill: str) -> List[AgentInstance]:
        return [self._agents[aid] for aid in self._by_skill.get(skill, set())]

    def find_by_name(self, name: str) -> List[AgentInstance]:
        return [a for a in self._agents.values() if a.config.name == name]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_agents": len(self._agents),
            "total_capabilities": len(self._by_capability),
            "total_skills": len(self._by_skill),
            "capabilities": {k: len(v) for k, v in self._by_capability.items()},
        }

    def export_cards(self) -> str:
        """Export all agent cards as JSON (for A2A discovery endpoint)."""
        return json.dumps(
            [c.to_dict() for c in self._cards.values()],
            indent=2,
        )
