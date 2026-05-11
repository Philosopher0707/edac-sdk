"""Agent Lifecycle — Spawner, health monitor, supervisor, crash recovery.

Every agent goes through a well-defined lifecycle:
    initializing → idle → working → [paused|error] → terminated

The spawner creates agents, the health monitor watches them,
the supervisor manages parent-child relationships and cascading cancellation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set
from uuid import uuid4

from edac.event.schema import AgentState, Event, EventType, EventPriority, create_event

logger = logging.getLogger("edac.agent.lifecycle")


# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────

@dataclass
class AgentConfig:
    """Configuration for an agent instance."""
    name: str
    agent_type: str = "generic"
    model: Optional[str] = None
    skills: List[str] = field(default_factory=list)
    goal: Optional[str] = None
    max_restarts: int = 3
    restart_window_seconds: float = 60.0
    heartbeat_interval: float = 5.0
    heartbeat_timeout: float = 15.0
    system_prompt: Optional[str] = None
    resource_limits: Dict[str, Any] = field(default_factory=dict)
    sandbox: bool = False
    parent_agent_id: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────
# Agent Instance
# ──────────────────────────────────────────────────────────────

class AgentInstance:
    """A running agent instance with state, health tracking, and lifecycle hooks."""

    def __init__(
        self,
        config: AgentConfig,
        agent_id: Optional[str] = None,
    ):
        self.config = config
        self.agent_id = agent_id or f"{config.name}-{uuid4().hex[:8]}"
        self.state = AgentState.INITIALIZING
        self.created_at = time.time()
        self.last_heartbeat = time.time()
        self.restart_count = 0
        self.last_restart = 0.0
        self.child_ids: Set[str] = set()
        self.task: Optional[asyncio.Task] = None
        self._state_handlers: Dict[AgentState, List[Callable[[], None]]] = {}
        self._lock = asyncio.Lock()

    # ── State Management ──

    async def transition(self, new_state: AgentState) -> None:
        async with self._lock:
            old = self.state
            self.state = new_state
            logger.info(f"Agent {self.agent_id}: {old.value} → {new_state.value}")
            for handler in self._state_handlers.get(new_state, []):
                try:
                    handler()
                except Exception as e:
                    logger.exception(f"State handler error for {self.agent_id}: {e}")

    def on_state(self, state: AgentState, handler: Callable[[], None]) -> None:
        self._state_handlers.setdefault(state, []).append(handler)

    # ── Health ──

    async def heartbeat(self) -> None:
        self.last_heartbeat = time.time()

    def is_healthy(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        elapsed = now - self.last_heartbeat
        if self.state in (AgentState.TERMINATED, AgentState.ERROR):
            return False
        return elapsed < self.config.heartbeat_timeout

    def can_restart(self) -> bool:
        if self.restart_count >= self.config.max_restarts:
            return False
        if time.time() - self.last_restart < self.config.restart_window_seconds:
            return False
        return True

    def record_restart(self) -> None:
        self.restart_count += 1
        self.last_restart = time.time()

    # ── Events ──

    def spawn_event(self) -> Event:
        return create_event(
            event_type=EventType.AGENT_SPAWN,
            source="system:lifecycle",
            topic=f"agent.{self.agent_id}.lifecycle",
            payload={
                "agent_id": self.agent_id,
                "agent_name": self.config.name,
                "agent_type": self.config.agent_type,
                "goal": self.config.goal,
                "parent_agent_id": self.config.parent_agent_id,
            },
            priority=EventPriority.NORMAL,
        )

    def heartbeat_event(self) -> Event:
        return create_event(
            event_type=EventType.AGENT_HEARTBEAT,
            source=f"agent:{self.agent_id}",
            topic=f"agent.{self.agent_id}.health",
            payload={"agent_id": self.agent_id, "state": self.state.value},
            priority=EventPriority.NORMAL,
        )

    def terminate_event(self, reason: str = "normal") -> Event:
        return create_event(
            event_type=EventType.AGENT_TERMINATE,
            source=f"agent:{self.agent_id}",
            topic=f"agent.{self.agent_id}.lifecycle",
            payload={"agent_id": self.agent_id, "reason": reason},
            priority=EventPriority.HIGH,
        )


# ──────────────────────────────────────────────────────────────
# Spawner
# ──────────────────────────────────────────────────────────────

class AgentSpawner(ABC):
    """Abstract agent spawner. Subclasses provide concrete creation logic."""

    @abstractmethod
    async def spawn(self, config: AgentConfig) -> AgentInstance:
        """Create and start a new agent instance."""
        ...

    @abstractmethod
    async def kill(self, agent: AgentInstance, reason: str = "requested") -> None:
        """Forcefully terminate an agent."""
        ...


class DefaultAgentSpawner(AgentSpawner):
    """Default spawner that runs agents as asyncio tasks."""

    def __init__(self, agent_factory: Callable[[AgentInstance], Coroutine[Any, Any, None]]):
        self.agent_factory = agent_factory

    async def spawn(self, config: AgentConfig) -> AgentInstance:
        agent = AgentInstance(config)
        await agent.transition(AgentState.INITIALIZING)

        # Start the agent coroutine as a task
        coro = self.agent_factory(agent)
        agent.task = asyncio.create_task(coro, name=f"agent-{agent.agent_id}")

        await agent.transition(AgentState.IDLE)
        return agent

    async def kill(self, agent: AgentInstance, reason: str = "requested") -> None:
        if agent.task and not agent.task.done():
            agent.task.cancel()
            try:
                await agent.task
            except asyncio.CancelledError:
                pass
        await agent.transition(AgentState.TERMINATED)
        logger.info(f"Agent {agent.agent_id} killed: {reason}")


# ──────────────────────────────────────────────────────────────
# Health Monitor
# ──────────────────────────────────────────────────────────────

class HealthMonitor:
    """Watches agent heartbeats and detects stalled/crashed agents."""

    def __init__(
        self,
        check_interval: float = 5.0,
        on_unhealthy: Optional[Callable[[AgentInstance], Coroutine[Any, Any, None]]] = None,
    ):
        self.check_interval = check_interval
        self.on_unhealthy = on_unhealthy
        self._agents: Dict[str, AgentInstance] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def __aenter__(self) -> HealthMonitor:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop(), name="health_monitor")
        logger.info("Health monitor started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health monitor stopped")

    def register(self, agent: AgentInstance) -> None:
        self._agents[agent.agent_id] = agent
        logger.debug(f"Health monitor registered {agent.agent_id}")

    def unregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        logger.debug(f"Health monitor unregistered {agent_id}")

    async def _monitor_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.check_interval)
            now = time.time()
            unhealthy = [
                a for a in list(self._agents.values())
                if not a.is_healthy(now)
            ]
            for agent in unhealthy:
                logger.warning(f"Agent {agent.agent_id} unhealthy (state={agent.state.value})")
                if self.on_unhealthy:
                    try:
                        await self.on_unhealthy(agent)
                    except Exception as e:
                        logger.exception(f"on_unhealthy callback error: {e}")


# ──────────────────────────────────────────────────────────────
# Supervisor
# ──────────────────────────────────────────────────────────────

class Supervisor:
    """Manages parent-child relationships and cascading cancellation."""

    def __init__(self, spawner: AgentSpawner):
        self.spawner = spawner
        self._agents: Dict[str, AgentInstance] = {}
        self._children: Dict[str, Set[str]] = {}  # parent_id -> child_ids
        self._lock = asyncio.Lock()

    async def create_agent(
        self,
        config: AgentConfig,
        parent_id: Optional[str] = None,
    ) -> AgentInstance:
        async with self._lock:
            if parent_id:
                config.parent_agent_id = parent_id
            agent = await self.spawner.spawn(config)
            self._agents[agent.agent_id] = agent
            if parent_id:
                self._children.setdefault(parent_id, set()).add(agent.agent_id)
            return agent

    async def terminate_agent(self, agent_id: str, reason: str = "requested") -> None:
        async with self._lock:
            agent = self._agents.pop(agent_id, None)
            if agent is None:
                return

            # Cascade: terminate children first
            children = self._children.pop(agent_id, set())
            for child_id in list(children):
                child = self._agents.get(child_id)
                if child:
                    await self.spawner.kill(child, reason="parent_terminated")
                    self._agents.pop(child_id, None)

            await self.spawner.kill(agent, reason)
            logger.info(f"Supervisor terminated {agent_id} ({reason})")

    async def pause_agent(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if agent:
            await agent.transition(AgentState.PAUSED)

    async def resume_agent(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if agent:
            await agent.transition(AgentState.IDLE)

    def get_agent(self, agent_id: str) -> Optional[AgentInstance]:
        return self._agents.get(agent_id)

    def list_agents(self) -> List[AgentInstance]:
        return list(self._agents.values())

    async def restart_agent(self, agent_id: str) -> Optional[AgentInstance]:
        agent = self._agents.get(agent_id)
        if agent is None:
            return None
        if not agent.can_restart():
            logger.error(f"Agent {agent_id} exceeded restart limit")
            return None

        config = agent.config
        parent_id = config.parent_agent_id
        await self.terminate_agent(agent_id, reason="restart")
        agent.record_restart()
        return await self.create_agent(config, parent_id=parent_id)
