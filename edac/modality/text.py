"""Text Modality Adapter."""

from __future__ import annotations

from typing import Any

from edac.modality.base import ModalityAdapter, ModalityContent
from edac.event.schema import ModalityType


class TextAdapter(ModalityAdapter):
    """Adapter for plain text content."""

    @property
    def modality(self) -> ModalityType:
        return ModalityType.TEXT

    def validate(self, content: ModalityContent) -> bool:
        return isinstance(content.data, str)

    def encode(self, raw: Any) -> ModalityContent:
        return ModalityContent(
            modality=ModalityType.TEXT,
            data=str(raw),
            metadata={},
            mime_type="text/plain",
        )

    def decode(self, content: ModalityContent) -> str:
        return str(content.data)
