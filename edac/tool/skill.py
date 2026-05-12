"""Skill Loader — Load markdown-based skills (Anthropic pattern).

Skills are lightweight behavior modules:
- YAML frontmatter for metadata (name, description, applies_when)
- Markdown body for instructions
- Optional Python scripts in core/ directory

Example skill file:
    ---
    name: python-refactor
    description: Refactor Python code for clarity and type safety.
    applies_when: Users request code refactoring for .py files.
    ---

    # Python Refactoring Skill

    ## Principles
    1. Preserve existing behavior.
    2. Add type hints where missing.
    ...
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("edac.tool.skill")


@dataclass
class Skill:
    """A loaded skill with metadata and body."""

    name: str
    description: str
    applies_when: str
    body: str
    metadata: Dict[str, Any]
    source_path: Optional[Path] = None

    def matches(self, query: str) -> bool:
        """Check if this skill applies to a user query."""
        q = query.lower()
        if q in self.applies_when.lower():
            return True
        if q in self.description.lower():
            return True
        if q in self.name.lower():
            return True
        return False


class SkillLoader:
    """Load and query markdown-based skills."""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def load(self, path: Path) -> Skill:
        """Load a single skill from a markdown file."""
        text = path.read_text(encoding="utf-8")
        skill = self._parse(text)
        skill.source_path = path
        self._skills[skill.name] = skill
        logger.info(f"Loaded skill: {skill.name} from {path}")
        return skill

    def load_directory(self, directory: Path) -> List[Skill]:
        """Load all .md files in a directory."""
        skills: List[Skill] = []
        for path in directory.glob("*.md"):
            try:
                skills.append(self.load(path))
            except Exception as e:
                logger.error(f"Failed to load skill {path}: {e}")
        return skills

    def _parse(self, text: str) -> Skill:
        """Parse frontmatter and body from markdown text."""
        # Match YAML frontmatter between --- delimiters
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
        if not match:
            raise ValueError("Skill file missing YAML frontmatter")

        frontmatter_text = match.group(1)
        body = match.group(2).strip()

        # Parse YAML frontmatter
        metadata: Dict[str, Any] = {}
        for line in frontmatter_text.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                metadata[key.strip()] = val.strip()

        name = metadata.get("name", "unnamed")
        description = metadata.get("description", "")
        applies_when = metadata.get("applies_when", "")

        return Skill(
            name=name,
            description=description,
            applies_when=applies_when,
            body=body,
            metadata=metadata,
        )

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_skills(self) -> List[Skill]:
        return list(self._skills.values())

    def find_matching(self, query: str) -> List[Skill]:
        """Find skills that match a query."""
        return [s for s in self._skills.values() if s.matches(query)]
