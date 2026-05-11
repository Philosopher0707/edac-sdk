"""Async HTTP client for the EDAC production server API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from edac.client.exceptions import EdacAPIError, EdacAuthError, EdacClientError, EdacNotFoundError
from edac.server.schemas import AgentInfo, CreateAgentRequest, HealthResponse, SubmitTaskRequest, TaskResponse


class EdacClient:
    """
    Async HTTP client for EDAC.

    Usage::

        async with EdacClient("http://localhost:8000", api_key="sekrit") as client:
            agent = await client.create_agent(CreateAgentRequest(name="worker"))
            agents = await client.list_agents()
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    # ── Internal helpers ──

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _handle_error(self, response: httpx.Response) -> None:
        """Raise typed exceptions for known error codes."""
        try:
            payload = response.json()
        except Exception:
            payload = {"detail": response.text or "Unknown error"}

        detail = payload.get("detail", "Unknown error")
        if response.status_code == 404:
            raise EdacNotFoundError(detail, status_code=404, response=payload)
        if response.status_code in (401, 403):
            raise EdacAuthError(detail, status_code=response.status_code, response=payload)
        raise EdacAPIError(detail, status_code=response.status_code, response=payload)

    # ── Agent CRUD ──

    async def create_agent(self, request: CreateAgentRequest) -> AgentInfo:
        """Create and start a new agent."""
        response = await self._client.post(
            self._url("/agents"),
            headers=self._headers(),
            json=request.model_dump(mode="json", exclude_none=True),
        )
        if response.status_code != 201:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    async def list_agents(self) -> List[AgentInfo]:
        """List all active agents."""
        response = await self._client.get(self._url("/agents"), headers=self._headers())
        if response.status_code != 200:
            self._handle_error(response)
        return [AgentInfo.model_validate(item) for item in response.json()]

    async def get_agent(self, agent_id: str) -> AgentInfo:
        """Get information about a single agent."""
        response = await self._client.get(
            self._url(f"/agents/{agent_id}"), headers=self._headers()
        )
        if response.status_code != 200:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    async def delete_agent(self, agent_id: str) -> Dict[str, str]:
        """Terminate and remove an agent."""
        response = await self._client.delete(
            self._url(f"/agents/{agent_id}"), headers=self._headers()
        )
        if response.status_code != 200:
            self._handle_error(response)
        return response.json()

    # ── Agent lifecycle ──

    async def restart_agent(self, agent_id: str) -> AgentInfo:
        """Restart an agent, preserving its configuration."""
        response = await self._client.post(
            self._url(f"/agents/{agent_id}/restart"), headers=self._headers()
        )
        if response.status_code != 200:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    async def pause_agent(self, agent_id: str) -> AgentInfo:
        """Pause a running agent."""
        response = await self._client.post(
            self._url(f"/agents/{agent_id}/pause"), headers=self._headers()
        )
        if response.status_code != 200:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    async def resume_agent(self, agent_id: str) -> AgentInfo:
        """Resume a paused agent."""
        response = await self._client.post(
            self._url(f"/agents/{agent_id}/resume"), headers=self._headers()
        )
        if response.status_code != 200:
            self._handle_error(response)
        return AgentInfo.model_validate(response.json())

    # ── Tasks ──

    async def submit_task(self, request: SubmitTaskRequest) -> TaskResponse:
        """Submit a new task to the system."""
        response = await self._client.post(
            self._url("/tasks"),
            headers=self._headers(),
            json=request.model_dump(mode="json", exclude_none=True),
        )
        if response.status_code != 200:
            self._handle_error(response)
        return TaskResponse.model_validate(response.json())

    # ── System ──

    async def get_health(self) -> HealthResponse:
        """Fetch the server health status."""
        response = await self._client.get(
            self._url("/health"), headers=self._headers()
        )
        if response.status_code != 200:
            self._handle_error(response)
        return HealthResponse.model_validate(response.json())

    # ── Context manager ──

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> EdacClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
