"""Video Modality Adapter — URL or base64 with keyframe timestamps."""

from __future__ import annotations

from typing import Any, List

from edac.modality.base import ModalityAdapter, ModalityContent
from edac.event.schema import ModalityType


class VideoAdapter(ModalityAdapter):
    """Adapter for video content."""

    @property
    def modality(self) -> ModalityType:
        return ModalityType.VIDEO

    def validate(self, content: ModalityContent) -> bool:
        return isinstance(content.data, (str, bytes))

    def encode(self, raw: Any, keyframes: List[float] = None, fmt: str = "mp4") -> ModalityContent:
        return ModalityContent(
            modality=ModalityType.VIDEO,
            data=str(raw),
            metadata={"keyframes": keyframes or [], "format": fmt},
            mime_type=f"video/{fmt}",
        )

    def decode(self, content: ModalityContent) -> str:
        return str(content.data)
