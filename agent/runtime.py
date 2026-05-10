"""Agent Runtime — Main entry point for agent execution.

The runtime wires together:
- EventBus (for communication)
- Supervisor (for lifecycle management)
- HealthMonitor (for crash detection)
- AgentRegistry (for discovery)
- Context manager and tool registry (injected into agents)

Usage:
    async with AgentRuntime(event_bus) as runtime:
        agent = await runtime.spawn(AgentConfig(name="coder", goal="Refactor auth"))
        result = await runtime.wait_for(agent, timeout=300)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable, Coroutine, Dict, List, Optional

from edac.event.bus import EventBus
from edac.agent.lifecycle import (
    AgentConfig,
    AgentInstance,
    AgentSpawner,
    DefaultAgentSpawner,
    HealthMonitor,
    Supervisor,
)
from edac.agent.registry import AgentCard, AgentRegistry

logger = logging.getLogger("edac.agent.runtime")


# ──────────────────────────────────────────────────────────────
# Runtime
# ──────────────────────────────────────────────────────────────

class AgentRuntime:
    """Manages the full agent lifecycle within an event-driven system."""

    def __init__(
        self,
        bus: EventBus,
        spawner: Optional[AgentSpawner] = None,
        registry: Optional[AgentRegistry] = None,
    ):
        self.bus = bus
        self.spawner = spawner or DefaultAgentSpawner(self._default_agent_factory)
        self.registry = registry or AgentRegistry()
        self.supervisor = Supervisor(self.spawner)
        self.health = HealthMonitor(
            on_unhealthy=self._on_unhealthy,
        )
        self._agent_factory: Optional[Callable[[AgentInstance], Coroutine[Any, Any, None]]] = None

    def set_agent_factory(
        self,
        factory: Callable[[AgentInstance], Coroutine[Any, Any, None]],
    ) -> None:
        """Set the coroutine factory used to run agent instances."""
        self._agent_factory = factory
        if isinstance(self.spawner, DefaultAgentSpawner):
            self.spawner.agent_factory = factory

    async def _default_agent_factory(self, agent: AgentInstance) -> None:
        """Fallback agent factory — emits idle heartbeat loop."""
        await agent.transition(agent.state)
        while agent.state not in ("terminated",):
            try:
                await asyncio.sleep(agent.config.heartbeat_interval)
                if agent.state == "terminated":
                    break
                await agent.heartbeat()
                await self.bus.emit(agent.heartbeat_event())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Agent {agent.agent_id} factory error: {e}")
                break

    # ── Lifecycle ──

    async def __aenter__(self) -> AgentRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

    async def start(self) -> None:
        await self.health.start()
        logger.info("Agent runtime started")

    async def stop(self) -> None:
        # Terminate all agents
        for agent in self.supervisor.list_agents():
            await self.supervisor.terminate_agent(agent.agent_id, reason="runtime_shutdown")
        await self.health.stop()
        logger.info("Agent runtime stopped")

    # ── Spawning ──

    async def spawn(
        self,
        config: AgentConfig,
        parent_id: Optional[str] = None,
        card: Optional[AgentCard] = None,
    ) -> AgentInstance:
        """Create and register a new agent."""
        agent = await self.supervisor.create_agent(config, parent_id=parent_id)
        self.health.register(agent)
        self.registry.register(agent, card)
        await self.bus.emit(agent.spawn_event())
        return agent

    async def kill(self, agent_id: str, reason: str = "requested") -> None:
        """Terminate an agent and clean up."""
        agent = self.supervisor.get_agent(agent_id)
        if agent:
            await self.bus.emit(agent.terminate_event(reason))
        await self.supervisor.terminate_agent(agent_id, reason)
        self.health.unregister(agent_id)
        self.registry.unregister(agent_id)

    async def restart(self, agent_id: str) -> Optional[AgentInstance]:
        """Restart an agent, preserving its config."""
        old = self.supervisor.get_agent(agent_id)
        if old is None:
            return None
        card = self.registry.get_card(agent_id)
        await self.kill(agent_id, reason="restart")
        return await self.spawn(old.config, parent_id=old.config.parent_agent_id, card=card)

    # ── Queries ──

    def get_agent(self, agent_id: str) -> Optional[AgentInstance]:
        return self.supervisor.get_agent(agent_id)

    def list_agents(self) -> List[AgentInstance]:
        return self.supervisor.list_agents()

    async def wait_for(
        self,
        agent: AgentInstance,
        target_states: Optional[List[str]] = None,
        timeout: Optional[float] = None,
    ) -> AgentInstance:
        """Wait until agent reaches a terminal or target state."""
        target_states = target_states or ["terminated", "error", "completed"]
        elapsed = 0.0
        interval = 0.1
        while timeout is None or elapsed < timeout:
            if agent.state.value in target_states:
                return agent
            await asyncio.sleep(interval)
            elapsed += interval
        raise TimeoutError(f"Agent {agent.agent_id} did not reach target state in {timeout}s")

    # ── Health callback ──

    async def _on_unhealthy(self, agent: AgentInstance) -> None:
        logger.warning(f"Runtime handling unhealthy agent {agent.agent_id}")
        if agent.can_restart():
            logger.info(f"Restarting unhealthy agent {agent.agent_id}")
            await self.restart(agent.agent_id)
        else:
            logger.error(f"Agent {agent.agent_id} failed permanently")
            await agent.transition(agent.state.__class__("error"))
            await self.kill(agent.agent_id, reason="unhealthy")
