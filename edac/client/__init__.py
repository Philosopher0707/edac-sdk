"""EDAC HTTP client SDK."""

from edac.client.client import EdacClient
from edac.client.exceptions import (
    EdacAPIError,
    EdacAuthError,
    EdacClientError,
    EdacNotFoundError,
    EdacRetryExhausted,
    EdacStreamError,
)
from edac.client.retry import RetryConfig
from edac.client.sync_client import EdacClientSync

__all__ = [
    "EdacClient",
    "EdacClientSync",
    "RetryConfig",
    "EdacClientError",
    "EdacAPIError",
    "EdacAuthError",
    "EdacNotFoundError",
    "EdacRetryExhausted",
    "EdacStreamError",
]
