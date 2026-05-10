"""Async runtime for EDAC.

Structured concurrency with anyio.
"""

from edac.runtime.asyncio import RuntimeConfig, TaskManager, runtime

__all__ = [
    "RuntimeConfig",
    "TaskManager",
    "runtime",
]
