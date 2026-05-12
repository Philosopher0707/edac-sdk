"""Tests for agent lifecycle, registry, and runtime."""

import asyncio
import pytest

from edac.agent.lifecycle import (
    AgentConfig,
    AgentInstance,
    DefaultAgentSpawner,
    HealthMonitor,
    Supervisor,
)
from edac.agent.registry import AgentCard, AgentRegistry
from edac.agent.runtime import AgentRuntime
from edac.event.bus import EventBus


class TestAgentLifecycle:
    def test_agent_config(self):
        cfg = AgentConfig(name="tester", goal="test things")
        assert cfg.name == "tester"
        assert cfg.goal == "test things"

    def test_agent_instance_creation(self):
        cfg = AgentConfig(name="tester")
        agent = AgentInstance(cfg)
        assert agent.agent_id.startswith("tester-")
        assert agent.state.value == "initializing"

    @pytest.mark.asyncio
    async def test_agent_transition(self):
        cfg = AgentConfig(name="tester")
        agent = AgentInstance(cfg)
        await agent.transition(agent.state.__class__("idle"))
        assert agent.state.value == "idle"

    def test_agent_health(self):
        cfg = AgentConfig(name="tester", heartbeat_timeout=1.0)
        agent = AgentInstance(cfg)
        assert agent.is_healthy()

    def test_agent_expired_health(self):
        cfg = AgentConfig(name="tester", heartbeat_timeout=0.01)
        agent = AgentInstance(cfg)
        import time

        time.sleep(0.02)
        assert not agent.is_healthy()

    def test_agent_can_restart(self):
        cfg = AgentConfig(name="tester", max_restarts=1)
        agent = AgentInstance(cfg)
        assert agent.can_restart()
        agent.record_restart()
        assert not agent.can_restart()

    def test_agent_events(self):
        cfg = AgentConfig(name="tester", goal="g")
        agent = AgentInstance(cfg)
        e = agent.spawn_event()
        assert e.event_type.value == "agent.spawn"
        assert e.payload["agent_name"] == "tester"

    @pytest.mark.asyncio
    async def test_default_spawner(self):
        async def factory(agent):
            await asyncio.sleep(0.01)

        spawner = DefaultAgentSpawner(factory)
        cfg = AgentConfig(name="tester")
        agent = await spawner.spawn(cfg)
        assert agent.state.value == "idle"
        assert agent.task is not None

        await spawner.kill(agent)
        assert agent.state.value == "terminated"


class TestHealthMonitor:
    @pytest.mark.asyncio
    async def test_detects_unhealthy(self):
        unhealthy = []

        async def on_unhealthy(agent):
            unhealthy.append(agent)

        monitor = HealthMonitor(check_interval=0.05, on_unhealthy=on_unhealthy)
        cfg = AgentConfig(name="tester", heartbeat_timeout=0.01)
        agent = AgentInstance(cfg)
        agent.last_heartbeat = 0  # force expired

        async with monitor:
            monitor.register(agent)
            await asyncio.sleep(0.1)

        assert len(unhealthy) >= 1
        assert unhealthy[0].agent_id == agent.agent_id


class TestSupervisor:
    @pytest.mark.asyncio
    async def test_create_and_terminate(self):
        async def factory(agent):
            while agent.state.value != "terminated":
                await asyncio.sleep(0.01)

        spawner = DefaultAgentSpawner(factory)
        sup = Supervisor(spawner)
        cfg = AgentConfig(name="tester")
        agent = await sup.create_agent(cfg)
        assert agent.agent_id in [a.agent_id for a in sup.list_agents()]

        await sup.terminate_agent(agent.agent_id)
        assert sup.get_agent(agent.agent_id) is None

    @pytest.mark.asyncio
    async def test_cascade_termination(self):
        async def factory(agent):
            while agent.state.value != "terminated":
                await asyncio.sleep(0.01)

        spawner = DefaultAgentSpawner(factory)
        sup = Supervisor(spawner)
        parent_cfg = AgentConfig(name="parent")
        parent = await sup.create_agent(parent_cfg)

        child_cfg = AgentConfig(name="child", parent_agent_id=parent.agent_id)
        child = await sup.create_agent(child_cfg, parent_id=parent.agent_id)

        await sup.terminate_agent(parent.agent_id)
        assert sup.get_agent(child.agent_id) is None

    @pytest.mark.asyncio
    async def test_pause_resume(self):
        async def factory(agent):
            await asyncio.sleep(0.1)

        spawner = DefaultAgentSpawner(factory)
        sup = Supervisor(spawner)
        cfg = AgentConfig(name="tester")
        agent = await sup.create_agent(cfg)

        await sup.pause_agent(agent.agent_id)
        assert sup.get_agent(agent.agent_id).state.value == "paused"

        await sup.resume_agent(agent.agent_id)
        assert sup.get_agent(agent.agent_id).state.value == "idle"


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        cfg = AgentConfig(name="tester")
        agent = AgentInstance(cfg)
        reg.register(agent)

        assert reg.get(agent.agent_id) is agent
        assert len(reg.list_agents()) == 1

    def test_find_by_name(self):
        reg = AgentRegistry()
        cfg = AgentConfig(name="tester")
        agent = AgentInstance(cfg)
        reg.register(agent)

        found = reg.find_by_name("tester")
        assert len(found) == 1
        assert found[0].agent_id == agent.agent_id

    def test_unregister(self):
        reg = AgentRegistry()
        cfg = AgentConfig(name="tester")
        agent = AgentInstance(cfg)
        reg.register(agent)
        reg.unregister(agent.agent_id)
        assert reg.get(agent.agent_id) is None

    def test_agent_card(self):
        card = AgentCard(name="tester", description="A test agent", capabilities=["coding"])
        d = card.to_dict()
        assert d["name"] == "tester"
        assert d["capabilities"] == ["coding"]


class TestHealthMonitorConcurrency:
    @pytest.mark.asyncio
    async def test_on_unhealthy_may_modify_agents(self):
        """on_unhealthy callback may unregister agent — must not crash iteration."""
        monitor = HealthMonitor(check_interval=0.05)
        cfg = AgentConfig(name="tester", heartbeat_timeout=0.01)
        agent = AgentInstance(cfg)
        agent.last_heartbeat = 0  # force expired

        unregistered = []

        async def on_unhealthy(a):
            unregistered.append(a.agent_id)
            monitor.unregister(a.agent_id)

        monitor.on_unhealthy = on_unhealthy

        async with monitor:
            monitor.register(agent)
            await asyncio.sleep(0.12)

        assert len(unregistered) >= 1


class TestAgentRuntime:
    @pytest.mark.asyncio
    async def test_runtime_lifecycle(self):
        bus = EventBus()
        runtime = AgentRuntime(bus)
        async with runtime:
            assert len(runtime.list_agents()) == 0

    @pytest.mark.asyncio
    async def test_spawn_agent(self):
        async def factory(agent):
            while agent.state.value != "terminated":
                await asyncio.sleep(0.01)

        bus = EventBus()
        runtime = AgentRuntime(bus)
        runtime.set_agent_factory(factory)

        async with bus:
            async with runtime:
                cfg = AgentConfig(name="tester")
                agent = await runtime.spawn(cfg)
                assert agent.agent_id in [a.agent_id for a in runtime.list_agents()]

                await runtime.kill(agent.agent_id)
                assert runtime.get_agent(agent.agent_id) is None

    @pytest.mark.asyncio
    async def test_restart_agent(self):
        async def factory(agent):
            while agent.state.value != "terminated":
                await asyncio.sleep(0.01)

        bus = EventBus()
        runtime = AgentRuntime(bus)
        runtime.set_agent_factory(factory)

        async with bus:
            async with runtime:
                cfg = AgentConfig(name="tester")
                agent = await runtime.spawn(cfg)
                old_id = agent.agent_id

                new_agent = await runtime.restart(old_id)
                assert new_agent is not None
                assert new_agent.agent_id != old_id
                assert new_agent.config.name == "tester"
