"""Artifact Modality Adapter — Files, binaries, structured outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from edac.modality.base import ModalityAdapter, ModalityContent
from edac.event.schema import ModalityType


class ArtifactAdapter(ModalityAdapter):
    """Adapter for binary artifact content."""

    @property
    def modality(self) -> ModalityType:
        return ModalityType.ARTIFACT

    def validate(self, content: ModalityContent) -> bool:
        return "mime_type" in content.metadata

    def encode(self, raw: Any, mime_type: str = "application/octet-stream", size_bytes: int = 0) -> ModalityContent:
        return ModalityContent(
            modality=ModalityType.ARTIFACT,
            data=str(raw),
            metadata={"mime_type": mime_type, "size_bytes": size_bytes},
            mime_type=mime_type,
        )

    def decode(self, content: ModalityContent) -> str:
        return str(content.data)
