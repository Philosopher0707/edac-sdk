"""MCP Client — Connect to Model Context Protocol servers.

Wraps MCP servers as EDAC tools so agents can use them seamlessly.
Supports dynamic tool discovery and typed parameter binding.

See: https://modelcontextprotocol.io/
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from edac.tool.registry import ToolRegistry, ToolSpec

logger = logging.getLogger("edac.tool.mcp")


@dataclass
class MCPConnection:
    """Descriptor for an MCP server connection."""
    name: str
    endpoint: str  # stdio command or http URL
    transport: str = "stdio"  # "stdio" or "http"
    tools_discovered: List[str] = None

    def __post_init__(self):
        if self.tools_discovered is None:
            self.tools_discovered = []


class MCPClient:
    """Client for discovering and calling tools from MCP servers.

    This is a lightweight adapter — for production use, integrate
    with the official MCP Python SDK.
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._connections: Dict[str, MCPConnection] = {}

    async def connect_stdio(self, name: str, command: str, args: Optional[List[str]] = None) -> MCPConnection:
        """Connect to an MCP server via stdio."""
        conn = MCPConnection(name=name, endpoint=command, transport="stdio")
        self._connections[name] = conn
        logger.info(f"MCP stdio connection registered: {name} ({command})")
        # TODO: spawn subprocess, perform handshake, discover tools
        return conn

    async def connect_http(self, name: str, url: str) -> MCPConnection:
        """Connect to an MCP server via HTTP/SSE."""
        conn = MCPConnection(name=name, endpoint=url, transport="http")
        self._connections[name] = conn
        logger.info(f"MCP HTTP connection registered: {name} ({url})")
        # TODO: perform handshake, discover tools
        return conn

    async def discover_tools(self, connection_name: str) -> List[ToolSpec]:
        """Discover tools from an MCP server and register them."""
        conn = self._connections.get(connection_name)
        if conn is None:
            raise ValueError(f"MCP connection '{connection_name}' not found")

        # Placeholder: in production, send JSON-RPC request to list_tools
        specs: List[ToolSpec] = []
        logger.info(f"Discovered {len(specs)} tools from {connection_name}")
        return specs

    async def call_tool(self, connection_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on an MCP server."""
        conn = self._connections.get(connection_name)
        if conn is None:
            raise ValueError(f"MCP connection '{connection_name}' not found")

        # Placeholder: in production, send JSON-RPC request
        logger.debug(f"MCP call {connection_name}/{tool_name}")
        return {"status": "ok", "placeholder": True}

    def disconnect(self, connection_name: str) -> None:
        self._connections.pop(connection_name, None)
        logger.info(f"MCP connection closed: {connection_name}")
