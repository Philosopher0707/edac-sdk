"""Skill Builder — Programmatic skill creation.

Usage:
    skill = (
        SkillBuilder()
        .name("python-refactor")
        .description("Refactor Python code.")
        .applies_when("Users request Python refactoring.")
        .principle("Preserve behavior.")
        .tool("read_file")
        .tool("edit_file")
        .build()
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from edac.tool.skill import Skill


@dataclass
class SkillBuilder:
    """Fluent builder for markdown-based skills."""

    _name: str = ""
    _description: str = ""
    _applies_when: str = ""
    _principles: List[str] = field(default_factory=list)
    _tools: List[str] = field(default_factory=list)
    _verification: List[str] = field(default_factory=list)
    _failure_recovery: List[str] = field(default_factory=list)

    def name(self, value: str) -> SkillBuilder:
        self._name = value
        return self

    def description(self, value: str) -> SkillBuilder:
        self._description = value
        return self

    def applies_when(self, value: str) -> SkillBuilder:
        self._applies_when = value
        return self

    def principle(self, value: str) -> SkillBuilder:
        self._principles.append(value)
        return self

    def tool(self, value: str) -> SkillBuilder:
        self._tools.append(value)
        return self

    def verification(self, value: str) -> SkillBuilder:
        self._verification.append(value)
        return self

    def failure_recovery(self, value: str) -> SkillBuilder:
        self._failure_recovery.append(value)
        return self

    def build(self) -> Skill:
        body_parts = []
        if self._principles:
            body_parts.append("## Principles\n" + "\n".join(f"{i+1}. {p}" for i, p in enumerate(self._principles)))
        if self._tools:
            body_parts.append("## Tools Available\n" + "\n".join(f"- `{t}`" for t in self._tools))
        if self._verification:
            body_parts.append("## Verification Steps\n" + "\n".join(f"{i+1}. {v}" for i, v in enumerate(self._verification)))
        if self._failure_recovery:
            body_parts.append("## Failure Recovery\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(self._failure_recovery)))

        body = "\n\n".join(body_parts)
        metadata = {
            "name": self._name,
            "description": self._description,
            "applies_when": self._applies_when,
        }
        return Skill(
            name=self._name,
            description=self._description,
            applies_when=self._applies_when,
            body=body,
            metadata=metadata,
        )
