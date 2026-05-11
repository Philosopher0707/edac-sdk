"""Tests for modality dispatch layer."""

import base64
from io import BytesIO

import pytest

from edac.event.schema import ModalityType
from edac.modality.dispatcher import ModalityDispatcher
from edac.modality.base import ModalityContent


class TestModalityDispatcher:
    """Unit tests for ModalityDispatcher."""

    def test_detect_text(self):
        dispatcher = ModalityDispatcher()
        assert dispatcher.detect("hello world") == ModalityType.TEXT

    def test_detect_code_by_shebang(self):
        dispatcher = ModalityDispatcher()
        assert dispatcher.detect("#!/usr/bin/env python\nprint(1)") == ModalityType.CODE

    def test_detect_code_by_extension(self):
        dispatcher = ModalityDispatcher()
        assert dispatcher.detect("x = 1", filename="test.py") == ModalityType.CODE

    def test_detect_image_base64(self):
        dispatcher = ModalityDispatcher()
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
        assert dispatcher.detect(b64) == ModalityType.IMAGE

    def test_detect_image_bytes(self):
        dispatcher = ModalityDispatcher()
        raw = b"\x89PNG\r\n\x1a\n"
        assert dispatcher.detect(raw) == ModalityType.IMAGE

    def test_detect_artifact_pdf(self):
        dispatcher = ModalityDispatcher()
        assert dispatcher.detect("x", filename="report.pdf") == ModalityType.ARTIFACT

    def test_detect_audio_wav(self):
        dispatcher = ModalityDispatcher()
        assert dispatcher.detect("x", filename="audio.wav") == ModalityType.AUDIO

    def test_detect_video_mp4(self):
        dispatcher = ModalityDispatcher()
        assert dispatcher.detect("x", filename="clip.mp4") == ModalityType.VIDEO

    def test_process_text(self):
        dispatcher = ModalityDispatcher()
        content = dispatcher.process("hello world")
        assert content.modality == ModalityType.TEXT
        assert content.data == "hello world"
        assert content.mime_type == "text/plain"

    def test_process_code(self):
        dispatcher = ModalityDispatcher()
        content = dispatcher.process("x = 1", filename="test.py")
        assert content.modality == ModalityType.CODE
        assert content.data == "x = 1"
        assert content.metadata["language"] == "python"

    def test_process_image(self):
        dispatcher = ModalityDispatcher()
        raw = b"\x89PNG\r\n\x1a\n"
        content = dispatcher.process(raw)
        assert content.modality == ModalityType.IMAGE
        assert content.mime_type == "image/png"

    def test_process_artifact(self):
        dispatcher = ModalityDispatcher()
        content = dispatcher.process("file contents", filename="report.pdf")
        assert content.modality == ModalityType.ARTIFACT
        assert content.metadata["mime_type"] == "application/pdf"

    def test_validate_good(self):
        dispatcher = ModalityDispatcher()
        content = ModalityContent(modality=ModalityType.TEXT, data="hello", metadata={}, mime_type="text/plain")
        assert dispatcher.validate(content)

    def test_validate_bad(self):
        dispatcher = ModalityDispatcher()
        content = ModalityContent(modality=ModalityType.TEXT, data=12345, metadata={}, mime_type="text/plain")
        assert not dispatcher.validate(content)

    def test_supported_modality_types(self):
        dispatcher = ModalityDispatcher()
        supported = dispatcher.supported_types()
        assert set(supported) == {
            ModalityType.TEXT,
            ModalityType.CODE,
            ModalityType.IMAGE,
            ModalityType.AUDIO,
            ModalityType.VIDEO,
            ModalityType.ARTIFACT,
        }

    def test_get_adapter(self):
        dispatcher = ModalityDispatcher()
        adapter = dispatcher.get_adapter(ModalityType.TEXT)
        assert adapter.modality == ModalityType.TEXT

    def test_get_adapter_unknown(self):
        dispatcher = ModalityDispatcher()
        assert dispatcher.get_adapter(ModalityType.EMBEDDING) is None


class TestModalityEndpoints:
    """HTTP integration tests for /modality/* routes."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from edac.server.api import create_app
        from edac.server.config import ServerConfig

        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            yield client

    def test_list_modalities(self, client):
        r = client.get("/modality")
        assert r.status_code == 200
        data = r.json()
        assert "types" in data
        assert set(data["types"]) == {"text", "code", "image", "audio", "video", "artifact"}

    def test_detect_text(self, client):
        r = client.post("/modality/detect", json={"data": "hello world"})
        assert r.status_code == 200
        assert r.json()["modality"] == "text"

    def test_detect_code(self, client):
        r = client.post("/modality/detect", json={"data": "x=1", "filename": "test.py"})
        assert r.status_code == 200
        assert r.json()["modality"] == "code"

    def test_detect_image(self, client):
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
        r = client.post("/modality/detect", json={"data": b64})
        assert r.status_code == 200
        assert r.json()["modality"] == "image"

    def test_process_text(self, client):
        r = client.post("/modality/process", json={"data": "hello", "filename": "note.txt"})
        assert r.status_code == 200
        data = r.json()
        assert data["modality"] == "text"
        assert data["mime_type"] == "text/plain"
        assert data["data"] == "hello"

    def test_process_image(self, client):
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
        r = client.post("/modality/process", json={"data": b64, "filename": "img.png"})
        assert r.status_code == 200
        data = r.json()
        assert data["modality"] == "image"
        assert data["mime_type"] == "image/png"

    def test_validate_good(self, client):
        r = client.post("/modality/validate", json={
            "modality": "text",
            "data": "hello",
            "metadata": {},
            "mime_type": "text/plain",
        })
        assert r.status_code == 200
        assert r.json()["valid"] is True

    def test_validate_bad(self, client):
        r = client.post("/modality/validate", json={
            "modality": "text",
            "data": 12345,
            "metadata": {},
            "mime_type": "text/plain",
        })
        assert r.status_code == 200
        assert r.json()["valid"] is False

    def test_stats(self, client):
        # prime counters via detect
        client.post("/modality/detect", json={"data": "hello"})
        client.post("/modality/detect", json={"data": "#!/usr/bin/env python\nprint(1)"})
        r = client.get("/modality/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["detected_total"] == 2
        assert data["processed_total"] == 0
        assert data["validated_total"] == 0
