"""A2A Bridge — Agent Card generation and discovery (Google A2A Protocol).

See: https://github.com/google/A2A
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from edac.agent.registry import AgentCard

logger = logging.getLogger("edac.protocol.a2a")


@dataclass
class A2ATask:
    """A2A task representation."""
    id: str
    status: str = "submitted"
    messages: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)


class A2ABridge:
    """Bridge to the A2A protocol for agent-to-agent communication."""

    def __init__(self, registry) -> None:
        self.registry = registry

    def generate_agent_card(self, agent_card: AgentCard) -> Dict[str, Any]:
        """Generate an A2A-compatible Agent Card."""
        return {
            "$schema": "https://google.github.io/A2A/schema#",
            "name": agent_card.name,
            "description": agent_card.description,
            "version": agent_card.version,
            "capabilities": {
                "streaming": True,
                "pushNotifications": False,
            },
            "skills": [
                {"name": s, "description": f"Skill: {s}"}
                for s in agent_card.skills
            ],
            "defaultInputModes": agent_card.input_modes,
            "defaultOutputModes": agent_card.output_modes,
            "endpoint": agent_card.endpoint or "/a2a",
        }

    def discover(self) -> str:
        """Export all agent cards as JSON."""
        cards = [self.generate_agent_card(c) for c in self.registry.list_cards()]
        return json.dumps(cards, indent=2)
