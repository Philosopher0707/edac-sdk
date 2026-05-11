"""Tests for the @agent(auto_register=True) decorator and registry integration."""

from __future__ import annotations

import asyncio

import pytest

from edac.agent.handler_registry import HandlerRegistry
from edac.agent.lifecycle import AgentConfig
from edac.agent.runtime import AgentRuntime
from edac.event.bus import EventBus
from edac.sdk.decorators import agent


class TestAgentDecoratorFunctional:
    """End-to-end tests: decorator → registry → runtime spawn."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset the global singleton so tests don't pollute each other."""
        old = HandlerRegistry._DEFAULT
        HandlerRegistry._DEFAULT = None
        yield
        HandlerRegistry._DEFAULT = old

    @pytest.mark.asyncio
    async def test_decorator_config_spawns_in_runtime(self):
        """A decorated factory auto-registers and spawns without set_agent_factory."""

        @agent(name="worker")
        async def my_worker(agent):
            while agent.state.value != "terminated":
                await asyncio.sleep(0.01)

        assert hasattr(my_worker, "_edac_agent_config")
        assert my_worker._edac_agent_config.name == "worker"

        bus = EventBus()
        runtime = AgentRuntime(bus)

        async with bus:
            async with runtime:
                cfg = AgentConfig(name="worker")
                instance = await runtime.spawn(cfg)
                assert instance is not None
                assert instance.config.name == "worker"
                # Factory resolved via HandlerRegistry matching config.name
                assert HandlerRegistry.get_default().get("worker") is my_worker
                assert instance.agent_id in [a.agent_id for a in runtime.list_agents()]

                await runtime.kill(instance.agent_id)
                # Wait briefly for task cleanup
                await asyncio.sleep(0.05)

    def test_decorator_registers_in_handler_registry(self):
        """The decorator registers its factory at decoration time."""

        @agent(name="counter")
        async def counter_agent(agent):
            pass

        registry = HandlerRegistry.get_default()
        assert "counter" in registry.list()
        assert registry.get("counter") is counter_agent
