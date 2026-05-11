"""Tests for approval gate endpoints and ToolRegistry integration."""

from __future__ import annotations

import pytest

from edac.human.approval import ApprovalGate, ApprovalManager
from edac.tool.registry import ToolRegistry, ToolSpec
from edac.event.bus import EventBus
from edac.event.schema import EventType


class TestApprovalManager:
    """Unit tests for ApprovalGate / ApprovalManager."""

    def test_gate_exact_match(self) -> None:
        gate = ApprovalGate(trigger_on="tool.git.push", prompt="Approve git push")
        assert gate.is_triggered("tool.git.push")
        assert not gate.is_triggered("tool.git.pull")

    def test_gate_wildcard_prefix(self) -> None:
        gate = ApprovalGate(trigger_on="tool.*", prompt="Any tool")
        assert gate.is_triggered("tool.rm")
        assert gate.is_triggered("tool.git.push")
        assert not gate.is_triggered("other.tool")

    def test_gate_star(self) -> None:
        gate = ApprovalGate(trigger_on="*", prompt="Everything")
        assert gate.is_triggered("anything")

    def test_approve_once(self) -> None:
        gate = ApprovalGate(
            trigger_on="tool.rm",
            prompt="Approve rm",
            required_approvers=1,
        )
        assert not gate.is_approved()
        assert gate.approve("alice")
        assert gate.is_approved()

    def test_approve_multi_required(self) -> None:
        gate = ApprovalGate(
            trigger_on="tool.deploy",
            prompt="Deploy?",
            required_approvers=2,
        )
        assert not gate.approve("alice")
        assert not gate.is_approved()
        assert gate.approve("bob")
        assert gate.is_approved()
        assert gate.approve("alice")  # already approved, still True

    def test_manager_check_returns_gate(self) -> None:
        mgr = ApprovalManager()
        gate = ApprovalGate(trigger_on="tool.*", prompt="Wildcard")
        mgr.add_gate(gate)
        assert mgr.check("tool.x") is gate
        assert mgr.check("other") is None

    def test_manager_approve(self) -> None:
        mgr = ApprovalManager()
        mgr.add_gate(ApprovalGate(trigger_on="tool.x", prompt="X", required_approvers=1))
        assert mgr.approve("tool.x", "alice")
        assert mgr.check("tool.x") is None


class TestToolRegistryApproval:
    """Integration tests for ToolRegistry + ApprovalManager."""

    @pytest.fixture
    async def tool_registry_with_approval(self) -> ToolRegistry:
        bus = EventBus()
        await bus.start()
        registry = ToolRegistry(bus=bus)
        mgr = ApprovalManager()
        mgr.add_gate(ApprovalGate(trigger_on="tool.destructive", prompt="Danger"))
        registry._approval_manager = mgr
        yield registry
        await bus.stop()

    @pytest.mark.asyncio
    async def test_destructive_tool_blocked(self, tool_registry_with_approval) -> None:
        registry = tool_registry_with_approval

        async def handler(x: int) -> int:
            return x

        registry.register(
            ToolSpec(name="destructive", description="bad", destructive=True),
            handler,
        )

        with pytest.raises(Exception, match="requires approval"):
            await registry.execute("destructive", {"x": 1})

    @pytest.mark.asyncio
    async def test_safe_tool_passes(self, tool_registry_with_approval) -> None:
        registry = tool_registry_with_approval

        async def handler(x: int) -> int:
            return x * 2

        registry.register(
            ToolSpec(name="safe", description="ok", destructive=False),
            handler,
        )
        result = await registry.execute("safe", {"x": 3})
        assert result == 6

    @pytest.mark.asyncio
    async def test_destructive_tool_allowed_after_approval(self, tool_registry_with_approval) -> None:
        registry = tool_registry_with_approval

        async def handler(x: int) -> int:
            return x

        registry.register(
            ToolSpec(name="destructive", description="bad", destructive=True),
            handler,
        )
        # approve
        assert registry._approval_manager is not None
        registry._approval_manager.approve("tool.destructive", "admin")
        result = await registry.execute("destructive", {"x": 5})
        assert result == 5
