"""Client-side exception hierarchy for the EDAC HTTP SDK."""

from __future__ import annotations

from typing import Any, Dict, Optional


class EdacClientError(Exception):
    """Base exception for all EDAC client errors."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, response: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response = response


class EdacAPIError(EdacClientError):
    """Server returned a 4xx/5xx response."""


class EdacAuthError(EdacClientError):
    """Authentication or authorization failure (401/403)."""


class EdacNotFoundError(EdacClientError):
    """Resource not found (404)."""


class EdacRetryExhausted(EdacClientError):
    """All retry attempts were exhausted."""

    def __init__(self, message: str, *, last_status_code: Optional[int] = None, attempts: int = 0) -> None:
        super().__init__(message, status_code=last_status_code)
        self.attempts = attempts


class EdacStreamError(EdacClientError):
    """Error during SSE or WebSocket streaming."""
