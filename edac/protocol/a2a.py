"""A2A Bridge — Agent Card generation and discovery (Google A2A Protocol).

See: https://github.com/google/A2A
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from edac.agent.registry import AgentCard

logger = logging.getLogger("edac.protocol.a2a")


class A2ATaskStatus(Enum):
    """A2A task lifecycle states."""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class A2AArtifact:
    """An artifact exchanged between agents (file, message, etc.)."""
    name: str
    parts: List[Dict[str, Any]] = field(default_factory=list)
    index: int = 0
    append: bool = False
    last_chunk: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parts": self.parts,
            "index": self.index,
            "append": self.append,
            "lastChunk": self.last_chunk,
            "metadata": self.metadata,
        }

    @classmethod
    def from_text(cls, name: str, text: str, **metadata: Any) -> A2AArtifact:
        return cls(
            name=name,
            parts=[{"type": "text", "text": text}],
            metadata=metadata,
        )

    @classmethod
    def from_file(cls, name: str, mime_type: str, data: bytes, **metadata: Any) -> A2AArtifact:
        import base64
        return cls(
            name=name,
            parts=[{"type": "file", "mimeType": mime_type, "bytes": base64.b64encode(data).decode()}],
            metadata=metadata,
        )


@dataclass
class A2AMessage:
    """A message in an A2A task."""
    role: str  # "user" or "agent"
    parts: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "parts": self.parts,
            "metadata": self.metadata,
        }

    @classmethod
    def from_text(cls, role: str, text: str, **metadata: Any) -> A2AMessage:
        return cls(role=role, parts=[{"type": "text", "text": text}], metadata=metadata)


@dataclass
class A2ATask:
    """A2A task representation with full lifecycle."""
    id: str
    status: A2ATaskStatus = field(default_factory=lambda: A2ATaskStatus.SUBMITTED)
    messages: List[A2AMessage] = field(default_factory=list)
    artifacts: List[A2AArtifact] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "messages": [m.to_dict() for m in self.messages],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "metadata": self.metadata,
        }

    def add_message(self, msg: A2AMessage) -> None:
        self.messages.append(msg)

    def add_artifact(self, art: A2AArtifact) -> None:
        self.artifacts.append(art)

    def transition(self, new_status: A2ATaskStatus) -> None:
        logger.debug(f"Task {self.id}: {self.status.value} -> {new_status.value}")
        self.status = new_status

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


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

    def get_card(self, name: str) -> Optional[Dict[str, Any]]:
        """Fetch a single agent card by name."""
        # Registry cards are keyed by agent_id; search by name instead.
        for card in self.registry.list_cards():
            if card.name == name:
                return self.generate_agent_card(card)
        return None

    # ── Task helpers ──

    def create_task(self, task_id: str, message: Optional[A2AMessage] = None) -> A2ATask:
        task = A2ATask(id=task_id)
        if message:
            task.add_message(message)
        return task
