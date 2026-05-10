"""MCP Client — Connect to Model Context Protocol servers.

Wraps MCP servers as EDAC tools so agents can use them seamlessly.
Supports dynamic tool discovery and typed parameter binding.

Uses the official MCP Python SDK for transport and session management.

See: https://modelcontextprotocol.io/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from edac.tool.registry import ToolRegistry, ToolSpec

logger = logging.getLogger("edac.tool.mcp")

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client, StdioServerParameters
except ImportError as _e:
    ClientSession = None  # type: ignore
    sse_client = None  # type: ignore
    stdio_client = None  # type: ignore
    StdioServerParameters = None  # type: ignore


@dataclass
class MCPConnection:
    """Descriptor for an MCP server connection."""

    name: str
    endpoint: str  # stdio command or http URL
    transport: str = "stdio"  # "stdio" or "http"
    tools_discovered: List[str] = field(default_factory=list)


class MCPClient:
    """Client for discovering and calling tools from MCP servers.

    Uses the official MCP Python SDK for stdio and SSE transports.
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._connections: Dict[str, MCPConnection] = {}
        self._sessions: Dict[str, Any] = {}  # ClientSession objects
        self._transports: Dict[str, Any] = {}  # transport context managers

    async def connect_stdio(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> MCPConnection:
        """Connect to an MCP server via stdio.

        Spawns a subprocess and communicates over stdin/stdout.
        """
        if stdio_client is None or ClientSession is None:
            raise RuntimeError("MCP SDK not installed. Install: pip install mcp")

        server = StdioServerParameters(command=command, args=args or [], env=env)
        transport = stdio_client(server)
        read, write = await transport.__aenter__()

        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()

        self._transports[name] = transport
        self._sessions[name] = session
        conn = MCPConnection(name=name, endpoint=command, transport="stdio")
        self._connections[name] = conn
        logger.info(f"MCP stdio connected: {name} ({command})")
        return conn

    async def connect_http(
        self,
        name: str,
        url: str,
        headers: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> MCPConnection:
        """Connect to an MCP server via HTTP/SSE.

        Uses Server-Sent Events for transport.
        """
        if sse_client is None or ClientSession is None:
            raise RuntimeError("MCP SDK not installed. Install: pip install mcp")

        transport = sse_client(url, headers=headers, timeout=timeout)
        read, write = await transport.__aenter__()

        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()

        self._transports[name] = transport
        self._sessions[name] = session
        conn = MCPConnection(name=name, endpoint=url, transport="http")
        self._connections[name] = conn
        logger.info(f"MCP HTTP connected: {name} ({url})")
        return conn

    async def discover_tools(self, connection_name: str) -> List[ToolSpec]:
        """Discover tools from an MCP server and register them."""
        conn = self._connections.get(connection_name)
        if conn is None:
            raise ValueError(f"MCP connection '{connection_name}' not found")

        session = self._sessions.get(connection_name)
        if session is None:
            raise RuntimeError(f"MCP session for '{connection_name}' is not active")

        result = await session.list_tools()
        specs: List[ToolSpec] = []

        for tool in result.tools:
            spec = ToolSpec(
                name=tool.name,
                description=tool.description or "",
                parameters=tool.inputSchema or {},
            )
            # Register a handler that proxies to the MCP server
            async def _make_handler(n: str = connection_name, t: str = tool.name):
                async def _handler(**kwargs) -> Any:
                    return await self.call_tool(n, t, kwargs)
                return _handler

            self.registry.register(spec, await _make_handler(), source="mcp")
            specs.append(spec)
            conn.tools_discovered.append(tool.name)

        logger.info(f"Discovered {len(specs)} tools from {connection_name}")
        return specs

    async def call_tool(
        self,
        connection_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Any:
        """Call a tool on an MCP server."""
        conn = self._connections.get(connection_name)
        if conn is None:
            raise ValueError(f"MCP connection '{connection_name}' not found")

        session = self._sessions.get(connection_name)
        if session is None:
            raise RuntimeError(f"MCP session for '{connection_name}' is not active")

        logger.debug(f"MCP call {connection_name}/{tool_name}")
        result = await session.call_tool(tool_name, arguments=arguments)
        return result

    async def disconnect(self, connection_name: str) -> None:
        """Close an MCP connection and clean up resources."""
        session = self._sessions.pop(connection_name, None)
        transport = self._transports.pop(connection_name, None)

        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                logger.warning(f"Error closing MCP session {connection_name}", exc_info=True)

        if transport is not None:
            try:
                await transport.__aexit__(None, None, None)
            except Exception:
                logger.warning(f"Error closing MCP transport {connection_name}", exc_info=True)

        self._connections.pop(connection_name, None)
        logger.info(f"MCP connection closed: {connection_name}")
