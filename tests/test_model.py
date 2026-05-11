"""Tests for model providers (Ollama, Anthropic, OpenAI)."""

import pytest

from edac.model import ChatMessage, ChatCompletion, ModelRegistry, StreamingChunk
from edac.model.ollama import OllamaProvider
from edac.model.anthropic import AnthropicProvider
from edac.model.openai import OpenAIProvider


class TestModelRegistry:
    def test_register_and_get(self):
        reg = ModelRegistry()
        prov = OllamaProvider()
        reg.register("ollama", prov)
        assert reg.get("ollama") is prov

    def test_get_unknown(self):
        reg = ModelRegistry()
        assert reg.get("unknown") is None

    @pytest.mark.asyncio
    async def test_get_available(self):
        reg = ModelRegistry()
        ollama = OllamaProvider()
        reg.register("ollama", ollama)
        # Ollama may or may not be running; just check it doesn't crash
        available = await reg.get_available()
        assert isinstance(available, list)

    @pytest.mark.asyncio
    async def test_get_default_fallback(self):
        reg = ModelRegistry()
        ollama = OllamaProvider()
        reg.register("ollama", ollama, fallback=True)
        default = await reg.get_default()
        assert default is ollama

    @pytest.mark.asyncio
    async def test_list_models(self):
        reg = ModelRegistry()
        ollama = OllamaProvider()
        reg.register("ollama", ollama)
        models = await reg.list_models()
        assert "ollama" in models

    @pytest.mark.asyncio
    async def test_chat_routing(self):
        reg = ModelRegistry()
        ollama = OllamaProvider()
        reg.register("ollama", ollama)
        # Skip integration tests unless explicitly requested
        pytest.skip("Integration test — requires running ollama server with models")


class TestChatMessage:
    def test_creation(self):
        m = ChatMessage(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.name is None

    def test_with_name(self):
        m = ChatMessage(role="assistant", content="ok", name="coder")
        assert m.name == "coder"


class TestChatCompletion:
    def test_creation(self):
        c = ChatCompletion(content="hello", model="test")
        assert c.content == "hello"
        assert c.model == "test"
        assert c.usage == {}
        assert c.finish_reason == ""

    def test_with_usage(self):
        c = ChatCompletion(content="hi", model="m", usage={"total_tokens": 10})
        assert c.usage["total_tokens"] == 10


class TestOllamaProvider:
    def test_name(self):
        p = OllamaProvider()
        assert p.name == "ollama"

    @pytest.mark.asyncio
    async def test_is_available_when_running(self):
        p = OllamaProvider()
        # Returns True if ollama is running, False otherwise
        assert isinstance(await p.is_available(), bool)

    def test_default_model(self):
        p = OllamaProvider(default_model="qwen2.5-coder")
        assert p.default_model == "qwen2.5-coder"

    def test_convert_messages(self):
        p = OllamaProvider()
        messages = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content="hi"),
            ChatMessage(role="tool", content="result"),
        ]
        converted = p._convert_messages(messages)
        assert len(converted) == 4
        assert converted[0]["role"] == "system"
        assert converted[1]["role"] == "user"
        assert converted[2]["role"] == "assistant"
        # tool messages map to assistant in Ollama
        assert converted[3]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_list_models(self):
        p = OllamaProvider()
        if not await p.is_available():
            pytest.skip("Ollama not running")
        models = await p.list_models()
        assert isinstance(models, list)
        if models:
            assert isinstance(models[0], str)

    @pytest.mark.asyncio
    async def test_chat(self):
        p = OllamaProvider()
        pytest.skip("Integration test — requires running ollama server with models")

    @pytest.mark.asyncio
    async def test_stream(self):
        p = OllamaProvider()
        pytest.skip("Integration test — requires running ollama server with models")

    @pytest.mark.asyncio
    async def test_embed(self):
        p = OllamaProvider()
        pytest.skip("Integration test — requires running ollama server with models")

    @pytest.mark.asyncio
    async def test_close(self):
        p = OllamaProvider()
        await p.close()
        assert p._session is None or p._session.closed


class TestAnthropicProvider:
    def test_name(self):
        p = AnthropicProvider(api_key="test")
        assert p.name == "anthropic"

    @pytest.mark.asyncio
    async def test_is_available_with_key(self):
        p = AnthropicProvider(api_key="sk-test")
        assert await p.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_without_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        p = AnthropicProvider(api_key="")
        assert await p.is_available() is False

    def test_convert_messages(self):
        p = AnthropicProvider(api_key="test")
        messages = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content="hi"),
            ChatMessage(role="tool", content="result"),
        ]
        system, converted = p._convert_messages(messages)
        assert system == "sys"
        assert len(converted) == 3
        assert converted[0]["role"] == "user"
        assert converted[1]["role"] == "assistant"
        assert converted[2]["role"] == "user"

    @pytest.mark.asyncio
    async def test_list_models(self):
        p = AnthropicProvider(api_key="test")
        models = await p.list_models()
        assert isinstance(models, list)
        assert len(models) > 0

    @pytest.mark.asyncio
    async def test_close(self):
        p = AnthropicProvider(api_key="test")
        await p.close()
        assert p._session is None or p._session.closed


class TestOpenAIProvider:
    def test_name(self):
        p = OpenAIProvider(api_key="test")
        assert p.name == "openai"

    @pytest.mark.asyncio
    async def test_is_available_with_key(self):
        p = OpenAIProvider(api_key="sk-test")
        assert await p.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_without_key(self):
        p = OpenAIProvider(api_key="")
        assert await p.is_available() is False

    def test_convert_messages(self):
        p = OpenAIProvider(api_key="test")
        messages = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content="hi"),
        ]
        converted = p._convert_messages(messages)
        assert len(converted) == 3
        assert converted[0]["role"] == "system"
        assert converted[1]["role"] == "user"
        assert converted[2]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_list_models(self):
        p = OpenAIProvider(api_key="test")
        models = await p.list_models()
        assert isinstance(models, list)
        assert len(models) > 0

    @pytest.mark.asyncio
    async def test_close(self):
        p = OpenAIProvider(api_key="test")
        await p.close()
        assert p._session is None or p._session.closed
