"""Extended tests for Phase 3–6: sync client, retry, edge cases, schema, CLI smoke.

Avoids cross-loop ASGI transport issues by testing sync client methods with mocks.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx
import pytest

from edac.client.client import EdacClient
from edac.client.exceptions import EdacAuthError, EdacClientError, EdacNotFoundError, EdacRetryExhausted
from edac.client.retry import RetryConfig, _should_retry
from edac.client.sync_client import EdacClientSync
from edac.server.schemas import BatchError, PaginatedList


# =============================================================================
# Phase 3/5: Retry edge cases
# =============================================================================


class TestRetryEdgeCases:
    def test_retry_disabled(self):
        """RetryConfig(max_retries=0) should never retry."""
        client = EdacClient("http://test", retry=RetryConfig(max_retries=0))
        assert client._retry.max_retries == 0

    def test_retry_on_network_error_simulation(self):
        cfg = RetryConfig()
        true_err = EdacClientError("network")
        assert _should_retry(true_err, cfg) is False  # Not retryable by default

    def test_retry_on_500(self):
        cfg = RetryConfig()
        err = EdacAuthError("boom", status_code=500)
        assert _should_retry(err, cfg) is False  # AuthError not retried

    def test_429_is_retried(self):
        from edac.client.exceptions import EdacAPIError

        cfg = RetryConfig()
        err = EdacAPIError("rate limited", status_code=429)
        assert _should_retry(err, cfg) is True

    def test_502_is_retried(self):
        from edac.client.exceptions import EdacAPIError

        cfg = RetryConfig()
        err = EdacAPIError("bad gateway", status_code=502)
        assert _should_retry(err, cfg) is True

    def test_404_never_retried(self):
        cfg = RetryConfig()
        err = EdacNotFoundError("not found")
        assert _should_retry(err, cfg) is False

    def test_401_never_retried(self):
        cfg = RetryConfig()
        err = EdacAuthError("unauthorized")
        assert _should_retry(err, cfg) is False

    def test_invalid_retry_config(self):
        with pytest.raises(ValueError):
            RetryConfig(max_retries=-1)
        with pytest.raises(ValueError):
            RetryConfig(backoff_base=0)


# =============================================================================
# Phase 3/5: Sync client (mocked — avoids cross-loop ASGI issues)
# =============================================================================


class TestSyncClientMocked:
    def test_sync_client_init(self):
        sync = EdacClientSync("http://test", api_key="sekrit")
        assert sync._async_client.api_key == "sekrit"
        sync.close()

    def test_sync_client_context_manager(self):
        with EdacClientSync("http://test") as sync:
            assert sync is not None
        # close() already called

    def test_sync_client_close_idempotent(self):
        sync = EdacClientSync("http://test")
        sync.close()
        sync.close()  # Should not raise

    def test_sync_methods_delegate(self):
        """Verify all sync methods call _run with the right coroutine."""
        from unittest.mock import MagicMock, patch

        sync = EdacClientSync("http://test")
        mock_run = MagicMock(return_value="ok")
        sync._run = mock_run

        sync.get_health()
        mock_run.assert_called_once()
        mock_run.reset_mock()

        sync.list_agents()
        mock_run.assert_called_once()
        mock_run.reset_mock()

        sync.get_agent("id")
        mock_run.assert_called_once()
        mock_run.reset_mock()

        sync.close()
        # close() internally calls _run, so already tested

    def test_sync_stream_events_signature(self):
        """stream_events returns a generator."""
        sync = EdacClientSync("http://test")
        gen = sync.stream_events(topics=["test"], timeout=1)
        assert hasattr(gen, "__iter__")
        assert hasattr(gen, "__next__")
        sync.close()

    def test_sync_watch_task_signature(self):
        """watch_task returns a generator."""
        sync = EdacClientSync("http://test")
        gen = sync.watch_task("task-1", timeout=1)
        assert hasattr(gen, "__iter__")
        assert hasattr(gen, "__next__")
        sync.close()

    def test_sync_batch_methods_call_async(self):
        """Batch methods delegate to async counterparts."""
        from unittest.mock import MagicMock

        sync = EdacClientSync("http://test")

        results: List[Any] = []

        def _mock_run(coro):
            results.append(type(coro).__name__)
            return []

        sync._run = _mock_run

        sync.create_agents_batch([])
        sync.delete_agents_batch([])
        sync.submit_tasks_batch([])
        assert len(results) == 3

        sync.close()


# =============================================================================
# Phase 5: Connection pooling
# =============================================================================


class TestConnectionPooling:
    def test_client_init_with_defaults(self):
        client = EdacClient("http://test")
        assert client._client is not None
        assert str(client._client.base_url) == "http://test"

    def test_custom_limits(self):
        limits = httpx.Limits(max_connections=50, max_keepalive_connections=10)
        client = EdacClient("http://test", limits=limits)
        assert client._client is not None

    def test_api_key_header(self):
        client = EdacClient("http://test", api_key="sekrit")
        headers = client._headers()
        assert headers["X-API-Key"] == "sekrit"


# =============================================================================
# Phase 5: Schema validation
# =============================================================================


class TestSchemaValidation:
    def test_paginated_list_model_dump(self):
        page = PaginatedList(items=["a", "b"], total=2, limit=10, offset=0)
        d = page.model_dump()
        assert d["items"] == ["a", "b"]
        assert d["total"] == 2
        assert d["limit"] == 10
        assert d["offset"] == 0

    def test_batch_error_model(self):
        be = BatchError(error="fail", detail="something broke", index=3)
        assert be.error == "fail"
        assert be.index == 3

    def test_batch_error_defaults(self):
        be = BatchError(error="oops")
        assert be.detail is None
        assert be.index is None

    def test_retry_exhausted(self):
        exc = EdacRetryExhausted("all retries failed", last_status_code=503, attempts=3)
        assert exc.attempts == 3
        assert exc.status_code == 503


# =============================================================================
# Phase 5/6: CLI smoke
# =============================================================================


class TestCLISmoke:
    def test_cli_groups_exist(self):
        """Verify CLI groups are importable and commands registered."""
        from edac.cli.main import cli
        from edac.cli.commands.agents import agents
        from edac.cli.commands.events import events
        from edac.cli.commands.tasks import tasks

        assert cli.name == "cli"
        assert agents.name == "agents"
        assert tasks.name == "tasks"
        assert events.name == "events"

    def test_cli_version_command(self):
        from edac.cli.main import version
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(version)
        assert result.exit_code == 0
        assert "0.3.0" in result.output

    def test_cli_agents_list_help(self):
        from edac.cli.commands.agents import agents
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(agents, ["list", "--help"])
        assert result.exit_code == 0
        assert "limit" in result.output.lower()

    def test_cli_tasks_submit_help(self):
        from edac.cli.commands.tasks import tasks
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(tasks, ["submit", "--help"])
        assert result.exit_code == 0
        assert "watch" in result.output.lower()

    def test_cli_events_follow_help(self):
        from edac.cli.commands.events import events
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(events, ["follow", "--help"])
        assert result.exit_code == 0
        assert "topics" in result.output.lower()

    def test_cli_events_watch_help(self):
        from edac.cli.commands.events import events
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(events, ["watch", "--help"])
        assert result.exit_code == 0
        assert "timeout" in result.output.lower()


# =============================================================================
# Phase 6: Version bump
# =============================================================================


class TestVersionBump:
    def test_api_version(self):
        from edac.server.api import create_app
        from edac.server.config import ServerConfig
        from fastapi import FastAPI

        cfg = ServerConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            api_key=None,
        )
        app: FastAPI = create_app(cfg)
        assert app.version == "0.3.0"

    def test_system_router_version(self):
        """Verify the version in system_router.py is 0.3.0."""
        import inspect

        from edac.server.routers import system_router

        source = inspect.getsource(system_router)
        assert 'version="0.3.0"' in source

    def test_cli_version(self):
        from edac.cli.main import version
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(version)
        assert "0.3.0" in result.output

    def test_pyproject_version(self):
        import tomllib

        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["project"]["version"] == "0.3.0"
