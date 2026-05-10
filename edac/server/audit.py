"""Audit logging for API calls and security events.

Logs to structured logger with request_id, user, action, resource, outcome.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from edac.server.tracing import get_request_id

logger = logging.getLogger("edac.server.audit")


@dataclass
class AuditEvent:
    timestamp: str
    action: str
    resource: str
    user: Optional[str]
    request_id: Optional[str]
    outcome: str
    details: Optional[Dict[str, Any]] = None


def log_audit(
    action: str,
    resource: str,
    outcome: str = "success",
    user: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Log a security audit event."""
    event = AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=action,
        resource=resource,
        user=user,
        request_id=get_request_id(),
        outcome=outcome,
        details=details,
    )
    logger.info(json.dumps(asdict(event)))
