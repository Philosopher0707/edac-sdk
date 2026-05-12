"""Handler Registry — synchronous, per-name mapping for agent factories.

The registry is intentionally **synchronous** because decorators such as
``@agent(auto_register=True)`` execute at import time and cannot ``await``.
The :class:`~edac.agent.runtime.AgentRuntime` resolver looks up factories
by ``config.name`` at spawn time (inside an async context, where sync IO is fine).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, List, Optional

if TYPE_CHECKING:
    from edac.agent.lifecycle import AgentInstance

logger = logging.getLogger("edac.agent.handler_registry")


class HandlerRegistry:
    """Synchronous registry mapping *agent name* → factory coroutine.

    .. rubric:: Factory contract
    A registered factory receives a single positional argument, the
    :class:`~edac.agent.lifecycle.AgentInstance` that has just been
    spawned, and is expected to run until the agent transitions to a
    terminal state (e.g. ``"terminated"``) or raises an unhandled
    exception.

    .. rubric:: Thread safety
    All mutating methods acquire an internal ``threading.Lock``, so
    it is safe to call ``register()`` / ``unregister()`` from multiple
    threads (or import-time decorators in different modules) without
    additional synchronisation.
    """

    _DEFAULT: Optional[HandlerRegistry] = None
    _DEFAULT_LOCK: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[["AgentInstance"], Coroutine[Any, Any, None]]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def register(
        self,
        name: str,
        factory: Callable[["AgentInstance"], Coroutine[Any, Any, None]],
    ) -> None:
        """Register *factory* for *name*.

        Idempotent — registering the **same** object twice is a no-op.
        Registering a **different** factory for an existing *name* raises
        :class:`ValueError` (preventing accidental overwrites).
        """
        with self._lock:
            existing = self._handlers.get(name)
            if existing is not None and existing is not factory:
                raise ValueError(f"Handler for agent name '{name}' is already registered")
            self._handlers[name] = factory
            logger.debug("Registered handler for agent '%s'", name)

    def get(self, name: str) -> Optional[Callable[["AgentInstance"], Coroutine[Any, Any, None]]]:
        """Return the registered factory for *name*, or ``None``."""
        with self._lock:
            return self._handlers.get(name)

    def list(self) -> List[str]:  # noqa: A003
        """Return all registered agent names."""
        with self._lock:
            return list(self._handlers.keys())

    def unregister(
        self, name: str
    ) -> Optional[Callable[["AgentInstance"], Coroutine[Any, Any, None]]]:
        """Remove a handler by *name*.

        Returns the removed factory, or ``None`` if *name* was not present.
        """
        with self._lock:
            handler = self._handlers.pop(name, None)
            if handler is not None:
                logger.debug("Unregistered handler for agent '%s'", name)
            return handler

    def clear(self) -> None:
        """Remove **all** handlers (useful primarily in tests)."""
        with self._lock:
            self._handlers.clear()

    # ------------------------------------------------------------------
    # Singleton façade — safe for import time registration
    # ------------------------------------------------------------------
    @classmethod
    def get_default(cls) -> HandlerRegistry:
        """Return the process-wide default registry (lazy, thread-safe)."""
        if cls._DEFAULT is None:
            with cls._DEFAULT_LOCK:
                # Double-checked locking
                if cls._DEFAULT is None:
                    cls._DEFAULT = cls()
        return cls._DEFAULT

    @classmethod
    def set_default(cls, registry: HandlerRegistry) -> None:
        """Replace the default registry (useful in tests)."""
        with cls._DEFAULT_LOCK:
            cls._DEFAULT = registry

    @classmethod
    def reset_default(cls) -> None:
        """Reset the default registry to a fresh instance (useful in tests)."""
        with cls._DEFAULT_LOCK:
            cls._DEFAULT = cls()
