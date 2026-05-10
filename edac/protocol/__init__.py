"""Protocol bridges for EDAC.

A2A, MCP, and SSE bridges for external interoperability.
"""

from edac.protocol.a2a import A2ABridge, A2ATask
from edac.protocol.mcp_bridge import MCPBridge
from edac.protocol.sse import SSEBridge

__all__ = [
    "A2ABridge",
    "A2ATask",
    "MCPBridge",
    "SSEBridge",
]
