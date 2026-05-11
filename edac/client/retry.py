"""Lightweight retry/backoff decorator for the EDAC async client.

Uses ``tenacity`` if available; falls back to a minimal custom implementation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Coroutine, Optional, Tuple, Type, Union

try:
    from tenacity import (
        retry as _tenacity_retry,
        retry_if_exception_type as _retry_if_exception_type,
        stop_after_attempt as _stop_after_attempt,
        wait_exponential as _wait_exponential,
    )

    _HAS_TENACITY = True
except Exception:  # pragma: no cover
    _HAS_TENACITY = False

from edac.client.exceptions import EdacAPIError, EdacAuthError, EdacClientError, EdacNotFoundError, EdacRetryExhausted

logger = logging.getLogger("edac.client.retry")

# Errors that should NEVER be retried
_NO_RETRY_ERRORS: Tuple[Type[Exception], ...] = (EdacAuthError, EdacNotFoundError, ValueError, TypeError)

# Status codes that are considered transient and safe to retry
_RETRY_STATUS_CODES: Tuple[int, ...] = (429, 500, 502, 503, 504)

# Network / transport errors that are safe to retry
_RETRY_NETWORK_ERRORS: Tuple[Type[Exception], ...] = ()

try:
    import httpx

    _RETRY_NETWORK_ERRORS = (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)
except Exception:  # pragma: no cover
    pass


@dataclass(frozen=True)
class RetryConfig:
    """Configuration for automatic retries with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (default: 3).
        backoff_base: Initial wait time in seconds between retries (default: 1.0).
        backoff_max: Maximum wait time in seconds between retries (default: 60.0).
        retry_statuses: HTTP status codes that trigger a retry (default: 429, 500, 502, 503, 504).
    """

    max_retries: int = 3
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    retry_statuses: Tuple[int, ...] = _RETRY_STATUS_CODES

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.backoff_base <= 0:
            raise ValueError("backoff_base must be > 0")


def _should_retry(exc: BaseException, cfg: RetryConfig) -> bool:
    """Determine whether *exc* is worth retrying under *cfg*."""
    # Never retry client-side programming errors or auth/not-found
    if isinstance(exc, _NO_RETRY_ERRORS):
        return False

    # Retry on specific API errors by status code
    if isinstance(exc, EdacAPIError) and exc.status_code is not None:
        return exc.status_code in cfg.retry_statuses

    # Retry on transient network errors
    if isinstance(exc, _RETRY_NETWORK_ERRORS):
        return True

    return False


def _wrap_with_custom_retry(
    cfg: RetryConfig,
) -> Callable[[Callable[..., Coroutine[Any, Any, Any]]], Callable[..., Coroutine[Any, Any, Any]]]:
    """Build a decorator that retries an async function using *cfg*."""

    def decorator(fn: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[BaseException] = None
            for attempt in range(cfg.max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except BaseException as exc:
                    last_exc = exc
                    if attempt == cfg.max_retries or not _should_retry(exc, cfg):
                        raise
                    wait = min(cfg.backoff_base * (2 ** attempt), cfg.backoff_max)
                    logger.debug(
                        "Retrying %s in %.2fs (attempt %d/%d): %s",
                        fn.__qualname__,
                        wait,
                        attempt + 1,
                        cfg.max_retries,
                        exc,
                    )
                    await asyncio.sleep(wait)

            # Exhausted all retries — raise a typed exception
            assert last_exc is not None
            status_code = getattr(last_exc, "status_code", None)
            raise EdacRetryExhausted(
                f"All {cfg.max_retries} retry attempts exhausted: {last_exc}",
                last_status_code=status_code,
                attempts=cfg.max_retries,
            ) from last_exc

        return wrapper

    return decorator


def retry_with_backoff(
    cfg: Optional[RetryConfig] = None,
) -> Callable[[Callable[..., Coroutine[Any, Any, Any]]], Callable[..., Coroutine[Any, Any, Any]]]:
    """Return a retry decorator compatible with ``tenacity`` (if installed) or a lightweight fallback.

    Usage::

        @retry_with_backoff(RetryConfig(max_retries=3))
        async def my_request(...) -> ...:
            ...
    """
    config = cfg or RetryConfig()

    if _HAS_TENACITY:
        return _tenacity_retry(  # type: ignore[return-value]
            stop=_stop_after_attempt(config.max_retries + 1),
            wait=_wait_exponential(multiplier=config.backoff_base, max=config.backoff_max),
            retry=_retry_if_exception_type(_RETRY_NETWORK_ERRORS + (EdacAPIError,)),
            reraise=True,
        )

    return _wrap_with_custom_retry(config)
