"""ModalityDispatcher — thin router over 6 modality adapters.

Provides:
  - detect(data, filename=None) → ModalityType
  - process(data, filename=None, **kwargs) → ModalityContent
  - validate(content) → bool
  - supported_types() → List[ModalityType]
  - get_adapter(modality) → Optional[ModalityAdapter]
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Any, Dict, List, Optional

from edac.event.schema import ModalityType
from edac.modality.base import ModalityAdapter, ModalityContent
from edac.modality.text import TextAdapter
from edac.modality.code import CodeAdapter
from edac.modality.image import ImageAdapter
from edac.modality.audio import AudioAdapter
from edac.modality.video import VideoAdapter
from edac.modality.artifact import ArtifactAdapter

logger = logging.getLogger("edac.modality.dispatcher")

# Extension → ModalityType mapping
_EXT_MAP: Dict[str, ModalityType] = {
    ".py": ModalityType.CODE,
    ".js": ModalityType.CODE,
    ".ts": ModalityType.CODE,
    ".go": ModalityType.CODE,
    ".rs": ModalityType.CODE,
    ".java": ModalityType.CODE,
    ".c": ModalityType.CODE,
    ".cpp": ModalityType.CODE,
    ".h": ModalityType.CODE,
    ".sh": ModalityType.CODE,
    ".rb": ModalityType.CODE,
    ".php": ModalityType.CODE,
    ".swift": ModalityType.CODE,
    ".kt": ModalityType.CODE,
    ".png": ModalityType.IMAGE,
    ".jpg": ModalityType.IMAGE,
    ".jpeg": ModalityType.IMAGE,
    ".gif": ModalityType.IMAGE,
    ".bmp": ModalityType.IMAGE,
    ".webp": ModalityType.IMAGE,
    ".svg": ModalityType.IMAGE,
    ".wav": ModalityType.AUDIO,
    ".mp3": ModalityType.AUDIO,
    ".ogg": ModalityType.AUDIO,
    ".flac": ModalityType.AUDIO,
    ".aac": ModalityType.AUDIO,
    ".mp4": ModalityType.VIDEO,
    ".avi": ModalityType.VIDEO,
    ".mov": ModalityType.VIDEO,
    ".mkv": ModalityType.VIDEO,
    ".webm": ModalityType.VIDEO,
    ".pdf": ModalityType.ARTIFACT,
    ".doc": ModalityType.ARTIFACT,
    ".docx": ModalityType.ARTIFACT,
    ".xls": ModalityType.ARTIFACT,
    ".xlsx": ModalityType.ARTIFACT,
    ".zip": ModalityType.ARTIFACT,
    ".tar": ModalityType.ARTIFACT,
    ".gz": ModalityType.ARTIFACT,
    ".json": ModalityType.ARTIFACT,
    ".xml": ModalityType.ARTIFACT,
    ".csv": ModalityType.ARTIFACT,
}

# MIME prefix → ModalityType
_MIME_PREFIX_MAP: Dict[str, ModalityType] = {
    "image/": ModalityType.IMAGE,
    "audio/": ModalityType.AUDIO,
    "video/": ModalityType.VIDEO,
    "text/": ModalityType.TEXT,
    "application/pdf": ModalityType.ARTIFACT,
    "application/zip": ModalityType.ARTIFACT,
    "application/json": ModalityType.ARTIFACT,
    "application/xml": ModalityType.ARTIFACT,
}


class ModalityDispatcher:
    """Thin router that detects modality from raw data/filename and delegates to adapters."""

    def __init__(self) -> None:
        self._adapters: Dict[ModalityType, ModalityAdapter] = {
            ModalityType.TEXT: TextAdapter(),
            ModalityType.CODE: CodeAdapter(),
            ModalityType.IMAGE: ImageAdapter(),
            ModalityType.AUDIO: AudioAdapter(),
            ModalityType.VIDEO: VideoAdapter(),
            ModalityType.ARTIFACT: ArtifactAdapter(),
        }
        self._counters: Dict[str, int] = {"detected": 0, "processed": 0, "validated": 0}

    # ── Detection ──

    def detect(self, data: Any, filename: Optional[str] = None) -> ModalityType:
        """Detect modality from raw data and optional filename."""
        self._counters["detected"] += 1

        # 1. Filename extension is the strongest signal
        if filename:
            ext = filename[filename.rfind("."):].lower() if "." in filename else ""
            if ext in _EXT_MAP:
                return _EXT_MAP[ext]
            # Fallback to mimetypes
            guessed, _ = mimetypes.guess_type(filename)
            if guessed:
                for prefix, mtype in _MIME_PREFIX_MAP.items():
                    if guessed.startswith(prefix) or guessed == prefix:
                        return mtype

        # 2. Check data type (bytes)
        if isinstance(data, bytes):
            return self._detect_bytes(data)

        # 3. Check data content (shebangs, base64 image headers)
        if isinstance(data, str):
            # Code shebang
            if data.startswith("#!/") or data.startswith("<?xml"):
                return ModalityType.CODE
            # Base64 image header check
            if self._looks_like_base64_image(data):
                return ModalityType.IMAGE
            # Code heuristics: common programming patterns
            if self._looks_like_code(data):
                return ModalityType.CODE

        # 4. Default
        return ModalityType.TEXT

    @staticmethod
    def _looks_like_code(data: str) -> bool:
        """Heuristic: check for common code patterns."""
        code_patterns = [
            "def ", "class ", "import ", "from ", "function ",
            "const ", "let ", "var ", "=>", "#include", "package ",
            "public ", "private ", "func ", "struct ", "impl ",
        ]
        return any(p in data for p in code_patterns)

    @staticmethod
    def _looks_like_base64_image(data: str) -> bool:
        """Heuristic: check if base64 string decodes to image magic bytes."""
        if len(data) < 12:
            return False
        try:
            header = base64.b64decode(data[:16])
        except Exception:
            return False
        image_magics = (b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")
        return any(header.startswith(m) for m in image_magics)

    @staticmethod
    def _detect_bytes(data: bytes) -> ModalityType:
        """Detect modality from raw bytes using magic headers."""
        image_magics = (b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")
        if any(data.startswith(m) for m in image_magics):
            return ModalityType.IMAGE
        audio_magics = (b"RIFF", b"ID3", b"OggS", b"fLaC")
        if any(data.startswith(m) for m in audio_magics):
            return ModalityType.AUDIO
        video_magics = (b"\x00\x00\x00", b"ftyp", b"moov")
        if any(data.startswith(m) for m in video_magics):
            return ModalityType.VIDEO
        pdf_magic = b"%PDF"
        if data.startswith(pdf_magic):
            return ModalityType.ARTIFACT
        return ModalityType.TEXT

    # ── Processing ──

    def process(self, data: Any, filename: Optional[str] = None, **kwargs: Any) -> ModalityContent:
        """Auto-detect modality and encode via the correct adapter."""
        self._counters["processed"] += 1
        modality = self.detect(data, filename=filename)
        adapter = self._adapters[modality]

        if modality == ModalityType.CODE:
            language = kwargs.get("language", self._language_from_filename(filename))
            return adapter.encode(data, language=language, filepath=filename or "")
        if modality == ModalityType.IMAGE:
            return adapter.encode(data, fmt=self._image_fmt_from_filename(filename))
        if modality == ModalityType.AUDIO:
            return adapter.encode(data, fmt=self._audio_fmt_from_filename(filename))
        if modality == ModalityType.VIDEO:
            return adapter.encode(data, fmt=self._video_fmt_from_filename(filename))
        if modality == ModalityType.ARTIFACT:
            mime = kwargs.get("mime_type", mimetypes.guess_type(filename or "")[0] or "application/octet-stream")
            return adapter.encode(data, mime_type=mime)

        # TEXT fallback
        return adapter.encode(data)

    @staticmethod
    def _language_from_filename(filename: Optional[str]) -> str:
        if not filename:
            return "python"
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".go": "go", ".rs": "rust", ".java": "java",
            ".c": "c", ".cpp": "cpp", ".h": "c", ".sh": "bash",
            ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
        }
        ext = filename[filename.rfind("."):].lower() if "." in filename else ""
        return ext_map.get(ext, "python")

    @staticmethod
    def _image_fmt_from_filename(filename: Optional[str]) -> str:
        if not filename:
            return "png"
        ext = filename[filename.rfind("."):].lower() if "." in filename else ""
        return ext.lstrip(".") or "png"

    @staticmethod
    def _audio_fmt_from_filename(filename: Optional[str]) -> str:
        if not filename:
            return "wav"
        ext = filename[filename.rfind("."):].lower() if "." in filename else ""
        return ext.lstrip(".") or "wav"

    @staticmethod
    def _video_fmt_from_filename(filename: Optional[str]) -> str:
        if not filename:
            return "mp4"
        ext = filename[filename.rfind("."):].lower() if "." in filename else ""
        return ext.lstrip(".") or "mp4"

    # ── Validation ──

    def validate(self, content: ModalityContent) -> bool:
        """Validate content using the correct adapter."""
        self._counters["validated"] += 1
        adapter = self._adapters.get(content.modality)
        if adapter is None:
            return False
        return adapter.validate(content)

    # ── Introspection ──

    def supported_types(self) -> List[ModalityType]:
        return list(self._adapters.keys())

    def get_adapter(self, modality: ModalityType) -> Optional[ModalityAdapter]:
        return self._adapters.get(modality)

    def stats(self) -> Dict[str, Any]:
        return {
            "supported_types": [m.value for m in self.supported_types()],
            "detected_total": self._counters["detected"],
            "processed_total": self._counters["processed"],
            "validated_total": self._counters["validated"],
        }
