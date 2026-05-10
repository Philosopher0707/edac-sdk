"""Base Modality Adapter — Unified interface for all content types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from edac.event.schema import ModalityType


@dataclass
class ModalityContent:
    """Content that can be any modality."""
    modality: ModalityType
    data: Any
    metadata: Dict[str, Any]
    mime_type: Optional[str] = None


class ModalityAdapter(ABC):
    """Base class for modality adapters."""

    @property
    @abstractmethod
    def modality(self) -> ModalityType:
        ...

    @abstractmethod
    def validate(self, content: ModalityContent) -> bool:
        ...

    @abstractmethod
    def encode(self, raw: Any) -> ModalityContent:
        ...

    @abstractmethod
    def decode(self, content: ModalityContent) -> Any:
        ...
