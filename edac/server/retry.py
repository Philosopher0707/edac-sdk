"""Retry decorator with exponential backoff and jitter."""

from __future__ import annotations

import asyncio
import logging
import random
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type

logger = logging.getLogger("edac.server.retry")


class RetryExhaustedError(Exception):
    """All retry attempts failed."""

    def __init__(self, message: str, last_exception: Optional[Exception] = None):
        super().__init__(message)
        self.last_exception = last_exception


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None,
) -> Callable:
    """Decorator for retry with exponential backoff.

    Args:
        max_attempts: Maximum retry attempts (including initial call).
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap.
        jitter: Add random jitter to delay.
        exceptions: Tuple of exception types to retry on.
        on_retry: Callback(err, attempt_number) called on each retry.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_err: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_err = e
                    if attempt >= max_attempts:
                        break
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if jitter:
                        delay = delay * (0.5 + random.random())
                    if on_retry:
                        try:
                            on_retry(e, attempt)
                        except Exception:
                            pass
                    logger.warning(
                        f"Retry {attempt}/{max_attempts} for {func.__name__} "
                        f"after {delay:.1f}s: {e}"
                    )
                    await asyncio.sleep(delay)
            raise RetryExhaustedError(
                f"{func.__name__} failed after {max_attempts} attempts",
                last_exception=last_err,
            )

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_err: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_err = e
                    if attempt >= max_attempts:
                        break
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if jitter:
                        delay = delay * (0.5 + random.random())
                    if on_retry:
                        try:
                            on_retry(e, attempt)
                        except Exception:
                            pass
                    logger.warning(
                        f"Retry {attempt}/{max_attempts} for {func.__name__} "
                        f"after {delay:.1f}s: {e}"
                    )
                    import time

                    time.sleep(delay)
            raise RetryExhaustedError(
                f"{func.__name__} failed after {max_attempts} attempts",
                last_exception=last_err,
            )

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
