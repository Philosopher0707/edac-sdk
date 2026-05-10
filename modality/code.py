"""Code Modality Adapter — with AST awareness placeholder."""

from __future__ import annotations

import hashlib
from typing import Any

from edac.modality.base import ModalityAdapter, ModalityContent
from edac.event.schema import ModalityType


class CodeAdapter(ModalityAdapter):
    """Adapter for code content with language metadata."""

    @property
    def modality(self) -> ModalityType:
        return ModalityType.CODE

    def validate(self, content: ModalityContent) -> bool:
        return isinstance(content.data, str) and "language" in content.metadata

    def encode(self, raw: Any, language: str = "python", filepath: str = "") -> ModalityContent:
        text = str(raw)
        return ModalityContent(
            modality=ModalityType.CODE,
            data=text,
            metadata={
                "language": language,
                "filepath": filepath,
                "ast_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
            },
            mime_type=f"text/x-{language}",
        )

    def decode(self, content: ModalityContent) -> str:
        return str(content.data)
