"""Anthropic Claude Provider — Cloud LLM via Anthropic API.

Requires ANTHROPIC_API_KEY environment variable.
Docs: https://docs.anthropic.com/claude/reference/messages_post
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from edac.model import ChatCompletion, ChatMessage, ModelProvider, StreamingChunk

logger = logging.getLogger("edac.model.anthropic")


class AnthropicProvider(ModelProvider):
    """Anthropic Claude model provider."""

    API_BASE = "https://api.anthropic.com/v1"

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "claude-sonnet-4-6",
        timeout: float = 120.0,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.default_model = default_model
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        return "anthropic"

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    def _convert_messages(self, messages: List[ChatMessage]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Extract system message, convert rest to Anthropic format."""
        system: Optional[str] = None
        converted: List[Dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system = m.content
            elif m.role == "tool":
                converted.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "content": m.content}],
                })
            else:
                converted.append({"role": m.role, "content": m.content})
        return system, converted

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
        system, conv_messages = self._convert_messages(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": conv_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        async with session.post(
            f"{self.API_BASE}/messages",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        content = ""
        if data.get("content"):
            content = data["content"][0].get("text", "")

        usage = data.get("usage", {})
        return ChatCompletion(
            content=content,
            model=model,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
            finish_reason=data.get("stop_reason", ""),
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
        system, conv_messages = self._convert_messages(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": conv_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            payload["system"] = system

        async with session.post(
            f"{self.API_BASE}/messages",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line = line.strip()
                if not line or not line.startswith(b"data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                delta = data.get("delta", {})
                content = delta.get("text", "")
                stop_reason = data.get("stop_reason")
                usage = data.get("usage")
                yield StreamingChunk(
                    content=content,
                    finish_reason=stop_reason,
                    usage=usage,
                )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
