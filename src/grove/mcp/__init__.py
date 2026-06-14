"""Grove MCP server — the workspace control plane over the Model Context Protocol.

Public surface:
    GroveMcpServer, McpServerConfig, main (the ``grove-mcp`` entry point)

Requires the ``mcp`` extra (``pip install 'grove[mcp]'``). The server
talks exclusively through ``grove.client.GroveClient`` — see CLAUDE.md
in this package for the boundary rule.
"""

from __future__ import annotations

from grove.mcp.server import GroveMcpServer, McpServerConfig, main

__all__ = ["GroveMcpServer", "McpServerConfig", "main"]
