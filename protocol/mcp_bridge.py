"""MCP Bridge — Expose EDAC tools as MCP servers.

Hosts EDAC tool registry as a Model Context Protocol server
so external clients can discover and call tools.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from edac.tool.registry import ToolRegistry, ToolSpec

logger = logging.getLogger("edac.protocol.mcp_bridge")


class MCPBridge:
    """Serves EDAC tools via the MCP protocol."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def list_tools(self) -> str:
        """JSON-RPC response listing available tools."""
        tools = self.registry.list_tools()
        result = {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.parameters,
                }
                for t in tools
            ]
        }
        return json.dumps(result)

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool and return JSON-RPC result."""
        # In a real implementation, this would be async and use the actual registry
        return json.dumps({
            "content": [{"type": "text", "text": f"Result from {name}"}],
            "isError": False,
        })
