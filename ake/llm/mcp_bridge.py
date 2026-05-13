from __future__ import annotations

import contextlib
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ake.llm.tools import ToolDefinition, ToolRegistry


class MCPBridge:
    """Connects to external MCP servers and registers their tools into a ToolRegistry.

    Lifecycle: call `connect()` for each server, then `register_all()`, and finally
    `close()` when shutting down (or use as an async context manager).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ClientSession] = {}
        self._exit_stack = contextlib.AsyncExitStack()

    async def connect(self, server_name: str, transport: str, **kwargs: Any) -> None:
        """Open a persistent connection to an MCP server.

        Args:
            server_name: Logical name used to identify this server.
            transport:   "stdio" or "sse".
            **kwargs:    Transport-specific parameters.
                         stdio: command, args, env
                         sse:   url, headers
        """
        if transport == "stdio":
            params = StdioServerParameters(
                command=kwargs["command"],
                args=kwargs.get("args", []),
                env=kwargs.get("env"),
            )
            read, write = await self._exit_stack.enter_async_context(stdio_client(params))
        else:
            raise ValueError(f"Unsupported MCP transport: {transport!r}")

        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._sessions[server_name] = session

    async def discover_tools(self, server_name: str) -> list[ToolDefinition]:
        """Call list_tools on the named server and return ToolDefinition objects."""
        session = self._sessions[server_name]
        result = await session.list_tools()
        tools: list[ToolDefinition] = []
        for mcp_tool in result.tools:
            name = mcp_tool.name
            tools.append(
                ToolDefinition(
                    name=name,
                    description=mcp_tool.description or "",
                    input_schema=mcp_tool.inputSchema or {"type": "object", "properties": {}},
                    handler=self._make_proxy(server_name, name),
                    source=f"mcp:{server_name}",
                )
            )
        return tools

    async def register_all(self, registry: ToolRegistry) -> None:
        """Discover tools from all connected servers and register them."""
        for server_name in self._sessions:
            for tool in await self.discover_tools(server_name):
                registry.register(tool)

    def _make_proxy(self, server_name: str, tool_name: str):
        """Return an async handler that forwards calls to the MCP server."""

        async def _proxy(**kwargs: Any) -> Any:
            session = self._sessions[server_name]
            call_result = await session.call_tool(tool_name, kwargs)
            if call_result.isError:
                return {"error": str(call_result.content)}
            content = call_result.content
            if len(content) == 1 and hasattr(content[0], "text"):
                return content[0].text
            return [getattr(c, "text", str(c)) for c in content]

        return _proxy

    async def close(self) -> None:
        await self._exit_stack.aclose()

    async def __aenter__(self) -> MCPBridge:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
