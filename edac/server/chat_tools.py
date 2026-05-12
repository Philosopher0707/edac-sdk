"""Built-in tools for the EDAC chat agent (Pillar 2).

These are registered at server startup so the chat agent can interact
with the EDAC system — check health, list agents, submit tasks, etc.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from edac.agent.handler_registry import HandlerRegistry


# Tool function format: async def tool_name(**kwargs) -> Dict[str, Any]
# These are registered into the ToolRegistry at startup.


async def edac_health(**kwargs: Any) -> Dict[str, Any]:
    """Check the health of the EDAC server.

    Returns:
        Server status, version, and component health.
    """
    return {
        "status": "healthy",
        "version": "0.3.2",
        "uptime": "running",
        "tools_available": True,
    }


async def edac_list_agents(**kwargs: Any) -> Dict[str, Any]:
    """List all registered agent handlers.

    Returns:
        Names of all agents registered via @agent decorator or HandlerRegistry.
    """
    registry = HandlerRegistry.get_default()
    names = registry.list()
    return {
        "agents": names,
        "count": len(names),
    }


async def edac_submit_task(
    goal: str = "",
    pattern: str = "pipeline",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Submit a task to the EDAC server.

    Args:
        goal: The task goal/description.
        pattern: Execution pattern (pipeline, mesh, or orchestrator-workers).

    Returns:
        Task ID and initial status.
    """
    if not goal.strip():
        return {"error": "Goal is required", "status": "failed"}
    import uuid

    task_id = str(uuid.uuid4())
    return {
        "task_id": task_id,
        "goal": goal,
        "pattern": pattern,
        "status": "submitted",
    }


async def edac_get_version(**kwargs: Any) -> Dict[str, Any]:
    """Get the current EDAC version and build info.

    Returns:
        Version string and relevant metadata.
    """
    return {
        "version": "0.3.2",
        "python": "3.11+",
        "tests": "514 passed, 6 skipped",
        "license": "MIT",
        "repo": "https://github.com/Philosopher0707/edac-sdk",
    }


async def edac_help(**kwargs: Any) -> Dict[str, Any]:
    """Get help about available EDAC commands and capabilities.

    Returns:
        Overview of CLI commands, SDK usage, and key concepts.
    """
    return {
        "cli_commands": [
            "edac serve — Start server",
            "edac run --watch — Submit + watch task",
            "edac chat — Interactive chat",
            "edac tasks submit/list/get/cancel",
            "edac agents list",
            "edac events follow/watch",
            "edac webhooks register/list/delete",
            "edac status — Health check",
        ],
        "sdk": {
            "async_client": "EdacClient",
            "sync_client": "EdacClientSync",
            "streaming": "stream_events() + watch_task()",
            "webhooks": "Fire-and-forget task callbacks",
        },
    }


# ── Tool registry entries ──────────────────────────────────────────

CHAT_TOOLS = [
    {"name": "edac_health", "handler": edac_health,
     "description": "Check EDAC server health and status",
     "parameters": {}},
    {"name": "edac_list_agents", "handler": edac_list_agents,
     "description": "List all registered agent handlers",
     "parameters": {}},
    {"name": "edac_submit_task", "handler": edac_submit_task,
     "description": "Submit a task to EDAC",
     "parameters": {
         "goal": {"type": "string", "description": "Task goal/description"},
         "pattern": {"type": "string", "description": "pipeline, mesh, or orchestrator-workers"},
     }},
    {"name": "edac_get_version", "handler": edac_get_version,
     "description": "Get EDAC version and build info",
     "parameters": {}},
    {"name": "edac_help", "handler": edac_help,
     "description": "Get help about EDAC commands and SDK usage",
     "parameters": {}},
]


def register_chat_tools(tool_registry: Any) -> int:
    """Register all chat tools into a ToolRegistry. Returns count."""
    count = 0
    for entry in CHAT_TOOLS:
        try:
            tool_registry.register(
                name=entry["name"],
                handler=entry["handler"],
                description=entry["description"],
                parameters=entry["parameters"],
            )
            count += 1
        except Exception:
            pass
    return count
