"""Audio Modality Adapter — base64 with transcript placeholder."""

from __future__ import annotations

import base64
from typing import Any

from edac.modality.base import ModalityAdapter, ModalityContent
from edac.event.schema import ModalityType


class AudioAdapter(ModalityAdapter):
    """Adapter for audio content with transcript metadata."""

    @property
    def modality(self) -> ModalityType:
        return ModalityType.AUDIO

    def validate(self, content: ModalityContent) -> bool:
        return isinstance(content.data, (str, bytes))

    def encode(self, raw: Any, transcript: str = "", fmt: str = "wav") -> ModalityContent:
        if isinstance(raw, bytes):
            data = base64.b64encode(raw).decode("ascii")
        else:
            data = str(raw)
        return ModalityContent(
            modality=ModalityType.AUDIO,
            data=data,
            metadata={"transcript": transcript, "format": fmt},
            mime_type=f"audio/{fmt}",
        )

    def decode(self, content: ModalityContent) -> bytes:
        if isinstance(content.data, bytes):
            return content.data
        return base64.b64decode(content.data)
