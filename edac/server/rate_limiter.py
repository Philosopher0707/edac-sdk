"""Token bucket rate limiter for API endpoints."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class Bucket:
    """Token bucket state."""

    capacity: float
    refill_rate: float  # tokens per second
    tokens: float = field(default=0.0)
    last_refill: float = field(default_factory=time.monotonic)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self):
        # Start with a full bucket
        self.tokens = self.capacity

    async def acquire(self, tokens: float = 1.0) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    async def wait_time(self, tokens: float = 1.0) -> float:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            if self.tokens >= tokens:
                return 0.0
            needed = tokens - self.tokens
            return needed / self.refill_rate


class RateLimiter:
    """Per-key token bucket rate limiter.

    Usage:
        limiter = RateLimiter(default_capacity=10, default_refill=1.0)
        if await limiter.acquire("client_123"):
            ...  # allowed
        else:
            ...  # rate limited
    """

    def __init__(
        self,
        default_capacity: float = 100.0,
        default_refill: float = 10.0,
    ):
        self.default_capacity = default_capacity
        self.default_refill = default_refill
        self._buckets: Dict[str, Bucket] = {}

    async def acquire(self, key: str, tokens: float = 1.0) -> bool:
        bucket = self._buckets.setdefault(
            key,
            Bucket(capacity=self.default_capacity, refill_rate=self.default_refill),
        )
        return await bucket.acquire(tokens)

    async def wait_time(self, key: str, tokens: float = 1.0) -> float:
        bucket = self._buckets.setdefault(
            key,
            Bucket(capacity=self.default_capacity, refill_rate=self.default_refill),
        )
        return await bucket.wait_time(tokens)

    def configure(self, key: str, capacity: float, refill_rate: float) -> None:
        self._buckets[key] = Bucket(capacity=capacity, refill_rate=refill_rate)
