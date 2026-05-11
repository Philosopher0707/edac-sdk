"""Shared utilities for EDAC examples.

Provides MockProvider so all examples run without external LLM dependencies.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from edac.model import ChatCompletion, ChatMessage, ModelProvider, ModelRegistry


class MockProvider(ModelProvider):
    """Deterministic mock LLM for runnable examples without API keys.

    Responds based on keyword matching in the prompt so examples produce
    realistic-looking output without network calls.
    """

    def __init__(
        self,
        name: str = "mock",
        responses: Optional[Dict[str, str]] = None,
        default_response: str = "Done.",
    ) -> None:
        self._name = name
        self._responses = responses or {}
        self._default = default_response
        self.last_messages: List[ChatMessage] = []

    @property
    def name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return True

    async def chat(self, messages: List[ChatMessage], **kwargs: Any) -> ChatCompletion:
        self.last_messages = messages
        prompt = " ".join(m.content for m in messages if m.content)
        prompt_lower = prompt.lower()

        # Keyword-driven responses for demo realism
        for keyword, response in self._responses.items():
            if keyword.lower() in prompt_lower:
                return ChatCompletion(content=response, model=self._name)

        return ChatCompletion(content=self._default, model=self._name)

    async def stream(self, messages: List[ChatMessage], **kwargs: Any) -> Any:
        pass

    async def close(self) -> None:
        pass


def make_registry_with_mock(
    responses: Optional[Dict[str, str]] = None,
    default_response: str = "Done.",
) -> ModelRegistry:
    """Create a ModelRegistry pre-configured with a MockProvider."""
    registry = ModelRegistry()
    registry.register("mock", MockProvider(responses=responses, default_response=default_response))
    return registry
