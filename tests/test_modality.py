"""Tests for modality adapters."""

import base64

import pytest

from edac.modality.base import ModalityContent
from edac.modality.text import TextAdapter
from edac.modality.code import CodeAdapter
from edac.modality.image import ImageAdapter
from edac.modality.audio import AudioAdapter
from edac.modality.video import VideoAdapter
from edac.modality.artifact import ArtifactAdapter
from edac.event.schema import ModalityType


class TestTextAdapter:
    def test_encode_decode(self):
        adapter = TextAdapter()
        content = adapter.encode("hello world")
        assert content.modality == ModalityType.TEXT
        assert adapter.validate(content)
        assert adapter.decode(content) == "hello world"

    def test_invalid(self):
        adapter = TextAdapter()
        content = ModalityContent(modality=ModalityType.TEXT, data=123, metadata={}, mime_type="")
        assert not adapter.validate(content)


class TestCodeAdapter:
    def test_encode_decode(self):
        adapter = CodeAdapter()
        content = adapter.encode("x = 1", language="python", filepath="test.py")
        assert content.modality == ModalityType.CODE
        assert content.metadata["language"] == "python"
        assert "ast_hash" in content.metadata
        assert adapter.decode(content) == "x = 1"

    def test_validate(self):
        adapter = CodeAdapter()
        valid = ModalityContent(
            modality=ModalityType.CODE, data="x=1", metadata={"language": "python"}, mime_type=""
        )
        assert adapter.validate(valid)
        invalid = ModalityContent(modality=ModalityType.CODE, data="x=1", metadata={}, mime_type="")
        assert not adapter.validate(invalid)


class TestImageAdapter:
    def test_encode_bytes(self):
        adapter = ImageAdapter()
        raw = b"\x89PNG\r\n\x1a\n"
        content = adapter.encode(raw, width=10, height=10, fmt="png")
        assert content.modality == ModalityType.IMAGE
        assert content.metadata["format"] == "png"
        decoded = adapter.decode(content)
        assert decoded == raw

    def test_encode_string(self):
        adapter = ImageAdapter()
        b64 = base64.b64encode(b"fake").decode("ascii")
        content = adapter.encode(b64, fmt="jpeg")
        decoded = adapter.decode(content)
        assert decoded == b"fake"


class TestAudioAdapter:
    def test_encode_decode(self):
        adapter = AudioAdapter()
        raw = b"RIFF"
        content = adapter.encode(raw, transcript="hello", fmt="wav")
        assert content.modality == ModalityType.AUDIO
        assert content.metadata["transcript"] == "hello"
        decoded = adapter.decode(content)
        assert decoded == raw


class TestVideoAdapter:
    def test_encode_decode(self):
        adapter = VideoAdapter()
        content = adapter.encode("http://example.com/video.mp4", keyframes=[0.0, 5.0], fmt="mp4")
        assert content.modality == ModalityType.VIDEO
        assert content.metadata["keyframes"] == [0.0, 5.0]
        assert adapter.decode(content) == "http://example.com/video.mp4"


class TestArtifactAdapter:
    def test_encode_decode(self):
        adapter = ArtifactAdapter()
        content = adapter.encode("/path/to/file.pdf", mime_type="application/pdf", size_bytes=1024)
        assert content.modality == ModalityType.ARTIFACT
        assert content.metadata["mime_type"] == "application/pdf"
        assert adapter.decode(content) == "/path/to/file.pdf"

    def test_validate(self):
        adapter = ArtifactAdapter()
        valid = ModalityContent(
            modality=ModalityType.ARTIFACT,
            data="x",
            metadata={"mime_type": "application/pdf"},
            mime_type="",
        )
        assert adapter.validate(valid)
        invalid = ModalityContent(
            modality=ModalityType.ARTIFACT, data="x", metadata={}, mime_type=""
        )
        assert not adapter.validate(invalid)
