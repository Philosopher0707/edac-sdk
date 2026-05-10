"""OpenAI GPT Provider — Cloud LLM via OpenAI-compatible API.

Works with:
- OpenAI (requires OPENAI_API_KEY)
- Azure OpenAI (requires AZURE_OPENAI_KEY + endpoint)
- Any OpenAI-compatible endpoint (e.g., LiteLLM proxy)

Requires OPENAI_API_KEY environment variable.
Docs: https://platform.openai.com/docs/api-reference/chat
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from edac.model import ChatCompletion, ChatMessage, ModelProvider, StreamingChunk

logger = logging.getLogger("edac.model.openai")


class OpenAIProvider(ModelProvider):
    """OpenAI-compatible model provider."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4o",
        timeout: float = 120.0,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        return "openai"

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            }
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    def _convert_messages(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """Convert ChatMessage list to OpenAI format."""
        return [
            {
                "role": m.role,
                "content": m.content,
                **({"name": m.name} if m.name else {}),
            }
            for m in messages
        ]

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
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        choice = data["choices"][0]
        message = choice.get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls")

        usage = data.get("usage", {})
        return ChatCompletion(
            content=content,
            model=data.get("model", model),
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=choice.get("finish_reason", ""),
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
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        async with session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line = line.strip()
                if not line or line == b"data: [DONE]":
                    continue
                if not line.startswith(b"data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                delta = data.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                finish = data.get("choices", [{}])[0].get("finish_reason")
                usage = data.get("usage")
                yield StreamingChunk(
                    content=content,
                    finish_reason=finish,
                    usage=usage,
                )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
