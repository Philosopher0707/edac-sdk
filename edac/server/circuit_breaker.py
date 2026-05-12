"""Circuit breaker for LLM providers and external services."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("edac.server.circuit_breaker")


class State(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject fast
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Circuit breaker with exponential backoff recovery.

    Args:
        name: Identifier for logging.
        failure_threshold: Failures before opening.
        recovery_timeout: Seconds before half-open.
        half_open_max_calls: Successful calls needed to close.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = State.CLOSED
        self._failures = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_successes = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        return self._state

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute func through the circuit breaker."""
        async with self._lock:
            if self._state == State.OPEN:
                if self._should_attempt_reset():
                    self._state = State.HALF_OPEN
                    self._half_open_successes = 0
                    logger.info(f"Circuit {self.name} half-open")
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit {self.name} is OPEN — last failure at {self._last_failure_time}"
                    )

        try:
            result = await func(*args, **kwargs)
            await self._record_success()
            return result
        except Exception as e:
            await self._record_failure()
            raise

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == State.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.half_open_max_calls:
                    self._state = State.CLOSED
                    self._failures = 0
                    logger.info(f"Circuit {self.name} closed")
            else:
                self._failures = max(0, self._failures - 1)

    async def _record_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()
            if self._state == State.HALF_OPEN:
                self._state = State.OPEN
                logger.warning(f"Circuit {self.name} open (half-open failure)")
            elif self._failures >= self.failure_threshold:
                self._state = State.OPEN
                logger.warning(f"Circuit {self.name} open ({self._failures} failures)")

    def _should_attempt_reset(self) -> bool:
        if self._last_failure_time is None:
            return True
        return (time.monotonic() - self._last_failure_time) >= self.recovery_timeout


class CircuitBreakerOpenError(Exception):
    """Circuit breaker is open — fast fail."""

    pass
