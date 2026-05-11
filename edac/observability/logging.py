"""Structured JSON logging with correlation IDs."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from edac.server.tracing import get_request_id


class CorrelationIdFilter(logging.Filter):
    """Inject correlation_id from tracing context into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        cid = get_request_id()
        if cid:
            record.correlation_id = cid
        elif not hasattr(record, "correlation_id"):
            record.correlation_id = None
        return True


class JSONFormatter(logging.Formatter):
    """Format log records as structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        obj: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "correlation_id") and record.correlation_id:
            obj["correlation_id"] = record.correlation_id
        if hasattr(record, "agent_id"):
            obj["agent_id"] = record.agent_id
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure structured JSON logging."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(CorrelationIdFilter())
    root = logging.getLogger("edac")
    root.handlers = [handler]
    root.setLevel(level)