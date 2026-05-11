"""Ollama Provider — Local LLM serving via HTTP API.

Requires ollama running locally:
    ollama serve
    ollama pull llama3.2
    ollama pull qwen2.5-coder

API docs: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from edac.model import ChatCompletion, ChatMessage, ModelProvider, StreamingChunk

logger = logging.getLogger("edac.model.ollama")


class OllamaProvider(ModelProvider):
    """Local Ollama model provider."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "llama3.2",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        return "ollama"

    async def is_available(self) -> bool:
        """Check if ollama server is reachable (async)."""
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _convert_messages(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """Convert ChatMessage list to Ollama format."""
        result = []
        for m in messages:
            if m.role == "tool":
                # Ollama uses "assistant" for tool responses in some versions
                result.append({"role": "assistant", "content": m.content})
            else:
                result.append({"role": m.role, "content": m.content})
        return result

    async def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        model = model or self.default_model
        session = await self._get_session()

        payload: Dict[str, Any] = {
            "model": model,
            "messages": self._convert_messages(messages),
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens
        if tools:
            payload["tools"] = tools

        async with session.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        message = data.get("message", {})
        content = message.get("content", "")

        return ChatCompletion(
            content=content,
            model=model,
            usage=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            finish_reason="stop" if not data.get("done_reason") else data.get("done_reason"),
            raw=data,
        )

    async def stream(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingChunk]:
        model = model or self.default_model
        session = await self._get_session()

        payload: Dict[str, Any] = {
            "model": model,
            "messages": self._convert_messages(messages),
            "stream": True,
            "options": {
                "temperature": temperature,
            },
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        async with session.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = data.get("message", {})
                content = message.get("content", "")
                done = data.get("done", False)
                yield StreamingChunk(
                    content=content,
                    finish_reason="stop" if done else None,
                )

    async def list_models(self) -> List[str]:
        """Return list of locally available models."""
        session = await self._get_session()
        async with session.get(f"{self.base_url}/api/tags") as resp:
            resp.raise_for_status()
            data = await resp.json()
        return [m["name"] for m in data.get("models", [])]

    async def embed(self, text: str, model: Optional[str] = None) -> List[float]:
        """Generate embeddings via Ollama."""
        session = await self._get_session()
        payload = {
            "model": model or self.default_model,
            "prompt": text,
        }
        async with session.post(f"{self.base_url}/api/embeddings", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("embedding", [])

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
