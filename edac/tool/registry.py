"""Tool Registry — Discovery, registration, and execution of tools.

Tools are the hands of agents. The registry:
- Registers tools from MCP servers, built-ins, and skills
- Discovers available tools dynamically
- Routes tool calls to the right implementation
- Tracks execution history and performance
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

from edac.event.bus import EventBus
from edac.event.schema import EventType, EventPriority, create_event

logger = logging.getLogger("edac.tool.registry")


# ──────────────────────────────────────────────────────────────
# Tool Descriptor
# ──────────────────────────────────────────────────────────────

@dataclass
class ToolSpec:
    """Metadata describing a tool's interface."""
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)  # JSON Schema-ish
    returns: Dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    sandbox_required: bool = False
    destructive: bool = False
    category: str = "general"


@dataclass
class ToolRecord:
    """A registered tool with its spec and handler."""
    spec: ToolSpec
    handler: Callable[..., Coroutine[Any, Any, Any]]
    source: str = "builtin"  # "builtin", "mcp", "skill"
    metadata: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────
# Tool Registry
# ──────────────────────────────────────────────────────────────

class ToolRegistry:
    """Central registry for tools with discovery and execution."""

    def __init__(self, bus: Optional[EventBus] = None):
        self.bus = bus
        self.secrets = None
        self._tools: Dict[str, ToolRecord] = {}
        self._by_category: Dict[str, List[str]] = {}
        self._execution_log: List[Dict[str, Any]] = []
        self._max_log_size = 10000

    # ── Registration ──

    def register(
        self,
        spec: ToolSpec,
        handler: Callable[..., Coroutine[Any, Any, Any]],
        source: str = "builtin",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._tools[spec.name] = ToolRecord(
            spec=spec,
            handler=handler,
            source=source,
            metadata=metadata or {},
        )
        self._by_category.setdefault(spec.category, []).append(spec.name)
        logger.info(f"Registered tool {spec.name} (source={source})")

    def unregister(self, name: str) -> Optional[ToolRecord]:
        record = self._tools.pop(name, None)
        if record:
            self._by_category.get(record.spec.category, []).remove(name)
        return record

    def get(self, name: str) -> Optional[ToolRecord]:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> List[ToolSpec]:
        return [r.spec for r in self._tools.values()]

    def list_by_category(self, category: str) -> List[ToolSpec]:
        return [self._tools[n].spec for n in self._by_category.get(category, [])]

    def search(self, query: str) -> List[ToolSpec]:
        q = query.lower()
        return [
            r.spec for r in self._tools.values()
            if q in r.spec.name.lower() or q in r.spec.description.lower()
        ]

    # ── Execution ──

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> Any:
        record = self._tools.get(name)
        if record is None:
            raise ToolNotFound(f"Tool '{name}' not found")

        start = time.time()
        error = None
        result = None

        await self._emit_tool_event(name, arguments, EventType.TOOL_CALL)

        # Resolve secret placeholders in arguments
        resolved = self._resolve_secrets(arguments, correlation_id)

        try:
            if record.spec.sandbox_required:
                from edac.security.sandbox import SecureSandbox
                sandbox = SecureSandbox()
                result = await sandbox.run(record.handler, resolved)
            else:
                result = await asyncio.wait_for(
                    record.handler(**resolved),
                    timeout=record.spec.timeout_seconds,
                )
        except asyncio.TimeoutError:
            error = f"Tool '{name}' timed out after {record.spec.timeout_seconds}s"
            await self._emit_tool_event(name, arguments, EventType.TOOL_TIMEOUT, error=error)
            raise ToolTimeout(error)
        except Exception as e:
            error = str(e)
            await self._emit_tool_event(name, arguments, EventType.TOOL_ERROR, error=error)
            raise ToolError(f"Tool '{name}' failed: {error}") from e
        finally:
            elapsed = time.time() - start
            self._log_execution(name, arguments, result, error, elapsed)

        await self._emit_tool_event(name, arguments, EventType.TOOL_RESULT, result=result)
        return result

    def _resolve_secrets(
        self,
        arguments: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Replace {{secret.key}} placeholders with values from SecretsManager."""
        if self.secrets is None:
            return arguments
        resolved: Dict[str, Any] = {}
        for k, v in arguments.items():
            if isinstance(v, str) and v.startswith("{{secret.") and v.endswith("}}"):
                key = v[9:-2]  # Extract key from {{secret.KEY}}
                scope = f"task:{correlation_id}" if correlation_id else None
                val = self.secrets.get(key, scope=scope) or self.secrets.get(key)
                resolved[k] = val if val is not None else v
            else:
                resolved[k] = v
        return resolved

    def _log_execution(
        self,
        name: str,
        arguments: Dict[str, Any],
        result: Any,
        error: Optional[str],
        elapsed: float,
    ) -> None:
        entry = {
            "tool": name,
            "arguments": arguments,
            "result": str(result)[:500] if result is not None else None,
            "error": error,
            "elapsed_ms": round(elapsed * 1000, 2),
        }
        self._execution_log.append(entry)
        if len(self._execution_log) > self._max_log_size:
            self._execution_log = self._execution_log[self._max_log_size // 2:]

    async def _emit_tool_event(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        event_type: EventType,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        if self.bus is None:
            return
        payload: Dict[str, Any] = {"tool_name": tool_name, "tool_input": arguments}
        if result is not None:
            payload["tool_output"] = str(result)[:1000]
        if error:
            payload["tool_error"] = error
        event = create_event(
            event_type=event_type,
            source=f"tool:{tool_name}",
            topic="tool.events",
            payload=payload,
            priority=EventPriority.HIGH if event_type in (EventType.TOOL_ERROR, EventType.TOOL_TIMEOUT) else EventPriority.NORMAL,
        )
        await self.bus.emit(event)

    def get_stats(self) -> Dict[str, Any]:
        total = len(self._execution_log)
        errors = sum(1 for e in self._execution_log if e.get("error"))
        return {
            "registered_tools": len(self._tools),
            "total_executions": total,
            "error_count": errors,
            "error_rate": errors / total if total else 0.0,
        }


# ──────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────

class ToolError(Exception):
    pass


class ToolNotFound(ToolError):
    pass


class ToolTimeout(ToolError):
    pass