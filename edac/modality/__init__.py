"""Modality adapters for EDAC.

Unified interface for text, code, image, audio, video, artifact.
"""

from edac.modality.base import ModalityAdapter, ModalityContent
from edac.modality.text import TextAdapter
from edac.modality.code import CodeAdapter
from edac.modality.image import ImageAdapter
from edac.modality.audio import AudioAdapter
from edac.modality.video import VideoAdapter
from edac.modality.artifact import ArtifactAdapter

__all__ = [
    "ModalityAdapter",
    "ModalityContent",
    "TextAdapter",
    "CodeAdapter",
    "ImageAdapter",
    "AudioAdapter",
    "VideoAdapter",
    "ArtifactAdapter",
    "ModalityDispatcher",
]
