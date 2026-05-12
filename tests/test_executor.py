"""Tests for AgentExecutor handler routing (Pillar 1)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from edac.agent.handler_registry import HandlerRegistry
from edac.agent.lifecycle import AgentConfig, AgentInstance
from edac.server.executor import AgentExecutor


class TestHandlerRouting:
    """AgentExecutor routes to HandlerRegistry when a custom handler exists."""

    @pytest.mark.asyncio
    async def test_custom_handler_invoked(self):
        """Registered handler is called with AgentInstance."""
        registry = HandlerRegistry()
        called_with = []

        async def my_handler(instance: AgentInstance) -> str:
            called_with.append(instance.config.name)
            return "custom result"

        registry.register("my-agent", my_handler)

        handler = registry.get("my-agent")
        assert handler is not None

        config = AgentConfig(name="my-agent")
        instance = AgentInstance(config=config)
        result = await handler(instance)

        assert "custom result" in result
        assert len(called_with) == 1
        assert called_with[0] == "my-agent"

    @pytest.mark.asyncio
    async def test_fallback_when_no_handler(self):
        """When no handler is registered, returns None (fallthrough to LLM)."""
        registry = HandlerRegistry()
        handler = registry.get("nonexistent")
        assert handler is None

    @pytest.mark.asyncio
    async def test_multiple_handlers_independent(self):
        """Multiple agents with different handlers don't interfere."""
        registry = HandlerRegistry()
        results = []

        async def handler_a(instance):
            results.append("a")

        async def handler_b(instance):
            results.append("b")

        registry.register("agent-a", handler_a)
        registry.register("agent-b", handler_b)

        a = registry.get("agent-a")
        b = registry.get("agent-b")

        assert a is not None
        assert b is not None
        assert a is not b

        await a(AgentInstance(config=AgentConfig(name="agent-a")))
        await b(AgentInstance(config=AgentConfig(name="agent-b")))

        assert results == ["a", "b"]
