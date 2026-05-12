"""Image Modality Adapter — base64 encoded with metadata."""

from __future__ import annotations

import base64
from typing import Any

from edac.modality.base import ModalityAdapter, ModalityContent
from edac.event.schema import ModalityType


class ImageAdapter(ModalityAdapter):
    """Adapter for image content (base64 or raw bytes)."""

    @property
    def modality(self) -> ModalityType:
        return ModalityType.IMAGE

    def validate(self, content: ModalityContent) -> bool:
        return isinstance(content.data, (str, bytes))

    def encode(
        self, raw: Any, width: int = 0, height: int = 0, fmt: str = "png"
    ) -> ModalityContent:
        if isinstance(raw, bytes):
            data = base64.b64encode(raw).decode("ascii")
        else:
            data = str(raw)
        return ModalityContent(
            modality=ModalityType.IMAGE,
            data=data,
            metadata={"width": width, "height": height, "format": fmt},
            mime_type=f"image/{fmt}",
        )

    def decode(self, content: ModalityContent) -> bytes:
        if isinstance(content.data, bytes):
            return content.data
        return base64.b64decode(content.data)
