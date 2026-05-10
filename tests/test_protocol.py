"""Tests for protocol bridges."""

import pytest

from edac.protocol.a2a import (
    A2AArtifact,
    A2AMessage,
    A2ATask,
    A2ATaskStatus,
    A2ABridge,
)
from edac.agent.registry import AgentCard, AgentRegistry
from edac.agent.lifecycle import AgentConfig, AgentInstance


class TestA2AArtifact:
    def test_from_text(self):
        art = A2AArtifact.from_text("readme", "hello world")
        assert art.name == "readme"
        assert art.parts[0]["text"] == "hello world"

    def test_to_dict(self):
        art = A2AArtifact.from_text("a", "b", version="1")
        d = art.to_dict()
        assert d["name"] == "a"
        assert d["lastChunk"] is True
        assert d["metadata"]["version"] == "1"

    def test_from_file(self):
        art = A2AArtifact.from_file("data", "application/json", b'{"x":1}')
        assert art.name == "data"
        assert art.parts[0]["type"] == "file"


class TestA2AMessage:
    def test_from_text(self):
        msg = A2AMessage.from_text("user", "hello")
        assert msg.role == "user"
        assert msg.parts[0]["text"] == "hello"

    def test_to_dict(self):
        msg = A2AMessage.from_text("agent", "ok", task="t1")
        d = msg.to_dict()
        assert d["role"] == "agent"
        assert d["metadata"]["task"] == "t1"


class TestA2ATask:
    def test_default_status(self):
        t = A2ATask(id="t1")
        assert t.status == A2ATaskStatus.SUBMITTED

    def test_add_message(self):
        t = A2ATask(id="t1")
        t.add_message(A2AMessage.from_text("user", "hello"))
        assert len(t.messages) == 1

    def test_add_artifact(self):
        t = A2ATask(id="t1")
        t.add_artifact(A2AArtifact.from_text("result", "done"))
        assert len(t.artifacts) == 1

    def test_transition(self):
        t = A2ATask(id="t1")
        t.transition(A2ATaskStatus.WORKING)
        assert t.status == A2ATaskStatus.WORKING

    def test_to_json(self):
        t = A2ATask(id="t1")
        text = t.to_json()
        assert "submitted" in text
        assert "t1" in text


class TestA2ABridge:
    def test_generate_agent_card(self):
        registry = AgentRegistry()
        bridge = A2ABridge(registry)
        card = AgentCard(name="coder", description="codes things", skills=["python"])
        d = bridge.generate_agent_card(card)
        assert d["name"] == "coder"
        assert d["skills"][0]["name"] == "python"

    def test_discover_empty(self):
        registry = AgentRegistry()
        bridge = A2ABridge(registry)
        assert bridge.discover() == "[]"

    def test_create_task(self):
        registry = AgentRegistry()
        bridge = A2ABridge(registry)
        task = bridge.create_task("task-1", A2AMessage.from_text("user", "go"))
        assert task.id == "task-1"
        assert len(task.messages) == 1
        assert task.status == A2ATaskStatus.SUBMITTED
