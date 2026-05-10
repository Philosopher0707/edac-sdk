"""Request tracing with correlation IDs.

Middleware injects X-Request-ID header, propagates through logs and events.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Optional

# Context var for current request ID
_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)


def get_request_id() -> Optional[str]:
    """Get current request correlation ID."""
    return _request_id_var.get()


def set_request_id(request_id: Optional[str] = None) -> str:
    """Set (or generate) request ID in current context."""
    rid = request_id or str(uuid.uuid4())[:8]
    _request_id_var.set(rid)
    return rid


def clear_request_id() -> None:
    """Clear request ID from context."""
    _request_id_var.set(None)
