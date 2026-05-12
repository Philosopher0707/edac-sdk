"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

# Add edac to path if needed
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Lifespan helpers — ASGITransport does not run startup/shutdown by default,
# so tests that hit endpoints through ``httpx.AsyncClient`` need to wrap the
# FastAPI application lifespan manually.
# ---------------------------------------------------------------------------


@pytest.fixture
def server_app():
    """Create a fresh FastAPI app with full lifespan for async tests."""
    from edac.server.api import create_app
    from edac.server.config import ServerConfig

    return create_app(config=ServerConfig(database_url="sqlite+aiosqlite:///:memory:"))


@pytest.fixture
def base_url():
    """Fixed base URL for tests using ASGITransport."""
    return "http://testserver"
