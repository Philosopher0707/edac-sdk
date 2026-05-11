"""Tests for new endpoints added in Phase 2 (MCP, A2A, SSE, tools)."""

import pytest
from fastapi.testclient import TestClient

from edac.server.api import create_app
from edac.server.config import ServerConfig


class TestMCPEndpoints:
    def test_mcp_list_tools_empty(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/mcp/tools")
            assert response.status_code == 200
            data = response.json()
            assert "tools" in data
            assert data["tools"] == []

    def test_mcp_call_tool_not_found(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.post("/mcp/tools/nonexistent", json={})
            assert response.status_code == 200
            data = response.json()
            assert data["isError"] is True
            assert "nonexistent" in data["content"][0]["text"]


class TestA2AEndpoints:
    def test_a2a_discover_empty(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/a2a/agents")
            assert response.status_code == 200
            data = response.json()
            assert data == []

    def test_a2a_agent_card_not_found(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/a2a/agents/bob/card")
            assert response.status_code == 404
            assert "bob" in response.json()["detail"]

    def test_a2a_create_task(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.post("/a2a/tasks", json={"id": "task-007"})
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == "task-007"
            assert data["status"] in ("submitted", "working")
            assert isinstance(data["messages"], list)
            assert isinstance(data["artifacts"], list)

    def test_a2a_create_task_with_message(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.post(
                "/a2a/tasks",
                json={
                    "id": "task-008",
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "hello"}],
                    },
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == "task-008"
            assert len(data["messages"]) == 1


class TestApprovalEndpoints:
    def test_list_gates_empty(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            response = client.get("/approvals/gates")
            assert response.status_code == 200
            data = response.json()
            # seeded gate exists
            assert len(data) == 1
            assert data[0]["trigger_on"] == "tool.*"
            assert data[0]["approved"] is False

    def test_create_gate(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            # create gate
            response = client.post(
                "/approvals/gates",
                json={"trigger_on": "tool.deploy", "prompt": "Deploy?"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["trigger_on"] == "tool.deploy"
            assert data["approved"] is False

            # list gates
            response = client.get("/approvals/gates")
            gates = response.json()
            assert len(gates) == 2  # seeded + new

    def test_approve_gate(self):
        app = create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))
        with TestClient(app) as client:
            # create gate
            client.post(
                "/approvals/gates",
                json={"trigger_on": "tool.deploy", "prompt": "Deploy?", "required_approvers": 1},
            )
            # approve
            response = client.post(
                "/approvals/gates/tool.deploy/approve",
                json={"approver": "admin"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["approved"] is True
            assert data["remaining"] == 0

            # list shows approved
            response = client.get("/approvals/gates")
            gates = response.json()
            deploy_gate = next(g for g in gates if g["trigger_on"] == "tool.deploy")
            assert deploy_gate["approved"] is True
            assert "admin" in deploy_gate["approvals"]
