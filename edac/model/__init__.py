"""Model Provider Abstraction — Unified interface for local and cloud LLMs.

Supports:
- Ollama (local)
- Anthropic Claude (cloud)
- OpenAI GPT (cloud)
- Any HTTP-compatible endpoint
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger("edac.model")


@dataclass
class ChatMessage:
    """A single message in a chat conversation."""
    role: str  # "system", "user", "assistant", "tool"
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


@dataclass
class ChatCompletion:
    """Result from a chat completion request."""
    content: str
    model: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamingChunk:
    """A single chunk from a streaming response."""
    content: str
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None


class ModelProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    async def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Send a chat completion request."""
        ...

    @abstractmethod
    async def stream(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingChunk]:
        """Stream a chat completion response."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and reachable."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        ...


class ModelRegistry:
    """Registry of available model providers."""

    def __init__(self) -> None:
        self._providers: Dict[str, ModelProvider] = {}
        self._fallback: Optional[str] = None

    def register(self, name: str, provider: ModelProvider, fallback: bool = False) -> None:
        self._providers[name] = provider
        if fallback:
            self._fallback = name
        logger.info(f"Registered model provider: {name}")

    def get(self, name: str) -> Optional[ModelProvider]:
        return self._providers.get(name)

    def get_available(self) -> List[str]:
        return [n for n, p in self._providers.items() if p.is_available()]

    def get_default(self) -> Optional[ModelProvider]:
        """Return first available provider, or fallback."""
        for name, provider in self._providers.items():
            if provider.is_available():
                return provider
        if self._fallback:
            return self._providers.get(self._fallback)
        return None

    async def list_models(self) -> Dict[str, List[str]]:
        """Return map of provider name -> available models."""
        result: Dict[str, List[str]] = {}
        for name, provider in self._providers.items():
            try:
                models = await provider.list_models()
                result[name] = models
            except Exception:
                result[name] = []
        return result

    async def chat(
        self,
        provider_name: str,
        messages: List[ChatMessage],
        **kwargs: Any,
    ) -> ChatCompletion:
        provider = self.get(provider_name)
        if provider is None:
            raise ValueError(f"Unknown provider: {provider_name}")
        return await provider.chat(messages, **kwargs)

    async def stream(
        self,
        provider_name: str,
        messages: List[ChatMessage],
        **kwargs: Any,
    ) -> AsyncIterator[StreamingChunk]:
        provider = self.get(provider_name)
        if provider is None:
            raise ValueError(f"Unknown provider: {provider_name}")
        async for chunk in provider.stream(messages, **kwargs):
            yield chunk
