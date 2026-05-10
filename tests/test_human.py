"""Tests for human interface layer."""

import asyncio

import pytest

from edac.human.hitl import HITLStateMachine, HITLTask, HITLState
from edac.human.approval import ApprovalGate, ApprovalManager
from edac.human.stream import EventStream, SSEStream
from edac.event.schema import Event, EventType, create_event


class TestHITLStateMachine:
    def test_create_task(self):
        sm = HITLStateMachine()
        task = sm.create_task("t1", "do something")
        assert task.state == HITLState.SUBMITTED
        assert sm.get_task("t1") is task

    def test_transition(self):
        sm = HITLStateMachine()
        sm.create_task("t1", "do something")
        sm.transition("t1", HITLState.WORKING)
        assert sm.get_task("t1").state == HITLState.WORKING

    @pytest.mark.asyncio
    async def test_request_and_provide_input(self):
        sm = HITLStateMachine()
        sm.create_task("t1", "do something")

        async def responder():
            await asyncio.sleep(0.05)
            sm.provide_input("t1", "user response")

        asyncio.create_task(responder())
        result = await sm.request_input("t1", "question?", timeout=1.0)
        assert result == "user response"
        assert sm.get_task("t1").state == HITLState.WORKING

    @pytest.mark.asyncio
    async def test_request_input_timeout(self):
        sm = HITLStateMachine()
        sm.create_task("t1", "do something")
        result = await sm.request_input("t1", "question?", timeout=0.05)
        assert result is None

    def test_complete(self):
        sm = HITLStateMachine()
        sm.create_task("t1", "do something")
        sm.complete("t1", "done")
        assert sm.get_task("t1").state == HITLState.COMPLETED
        assert sm.get_task("t1").result == "done"

    def test_fail(self):
        sm = HITLStateMachine()
        sm.create_task("t1", "do something")
        sm.fail("t1", "error")
        assert sm.get_task("t1").state == HITLState.FAILED
        assert sm.get_task("t1").error == "error"


class TestApprovalGate:
    def test_trigger_exact(self):
        gate = ApprovalGate(trigger_on="tool.git.push", prompt="approve?")
        assert gate.is_triggered("tool.git.push")
        assert not gate.is_triggered("tool.file.read")

    def test_trigger_wildcard(self):
        gate = ApprovalGate(trigger_on="tool.git.*", prompt="approve?")
        assert gate.is_triggered("tool.git.push")
        assert not gate.is_triggered("tool.file.read")

    def test_approve(self):
        gate = ApprovalGate(trigger_on="*", prompt="approve?", required_approvers=2)
        assert not gate.is_approved()
        gate.approve("alice")
        assert not gate.is_approved()
        gate.approve("bob")
        assert gate.is_approved()


class TestApprovalManager:
    def test_check_no_gate(self):
        mgr = ApprovalManager()
        assert mgr.check("tool.file.read") is None

    def test_check_triggered(self):
        mgr = ApprovalManager()
        gate = ApprovalGate(trigger_on="tool.git.push", prompt="approve?")
        mgr.add_gate(gate)
        triggered = mgr.check("tool.git.push")
        assert triggered is not None
        assert triggered.prompt == "approve?"

    def test_approve_action(self):
        mgr = ApprovalManager()
        gate = ApprovalGate(trigger_on="tool.git.push", prompt="approve?")
        mgr.add_gate(gate)
        mgr.approve("tool.git.push", "alice")
        assert mgr.check("tool.git.push") is None  # now approved


class TestEventStream:
    @pytest.mark.asyncio
    async def test_emit_and_iterate(self):
        stream = EventStream()
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={})

        async def consumer():
            events = []
            async for ev in stream:
                events.append(ev)
                break
            return events

        task = asyncio.create_task(consumer())
        await stream.emit(e)
        await asyncio.sleep(0.05)
        await stream.close()
        events = await task
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_sse_format(self):
        stream = EventStream()
        sse = SSEStream(stream)
        e = create_event(EventType.AGENT_SPAWN, "agent:a", "agent.spawn", payload={"x": 1})

        async def consumer():
            lines = []
            async for line in sse:
                lines.append(line)
                break
            return lines

        task = asyncio.create_task(consumer())
        await stream.emit(e)
        await asyncio.sleep(0.05)
        await stream.close()
        lines = await task
        assert len(lines) > 0
        assert "data:" in lines[0]
        assert "agent.spawn" in lines[0]
