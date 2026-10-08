"""Post 05, sections 3, 5 and 10: what the SDK says when a first server is written wrong.

Run from the series root:
    uv run python posts/05-first-server/snippets/sdk_errors.py

Each case is attempted against the installed mcp==2.3.0 and the exception it
raises is printed as `Type: message`, or "no error" when there is none. Nothing is served and nothing is left
running: the server objects below are built and thrown away.
"""

from __future__ import annotations

import asyncio
import importlib
import json

from mcp import Client
from mcp.server.mcpserver import MCPServer


def attempt(label: str, action) -> None:
    try:
        action()
        print(f"{label}\n    no error")
    except Exception as exc:  # noqa: BLE001 - printing the failure is the point
        print(f"{label}\n    {type(exc).__name__}: {exc}")


def tool_without_parentheses() -> None:
    server = MCPServer("broken")

    @server.tool  # the mistake: no parentheses
    def ping() -> str:
        return "pong"


def resource_without_parentheses() -> None:
    server = MCPServer("broken")

    @server.resource  # the mistake: no parentheses and no URI
    def readme() -> str:
        return "hello"


def prompt_without_parentheses() -> None:
    server = MCPServer("broken")

    @server.prompt  # the mistake: no parentheses
    def greet() -> str:
        return "hello"


print("== code written for the 1.x SDK ==")
for module in ("mcp.server.fastmcp", "mcp.types", "mcp.server.mcpserver", "mcp_types"):
    attempt(f"import {module}", lambda m=module: importlib.import_module(m))
mirror = importlib.import_module("mcp.types")
wire = importlib.import_module("mcp_types")
print(f"mcp.types.Tool is mcp_types.Tool: {mirror.Tool is wire.Tool}")

print("\n== decorators used without being called ==")
attempt("@mcp.tool", tool_without_parentheses)
attempt("@mcp.resource", resource_without_parentheses)
attempt("@mcp.prompt", prompt_without_parentheses)

print("\n== transport settings handed to the constructor ==")
attempt('MCPServer("x", port=9000)', lambda: MCPServer("x", port=9000))

print("\n== a parameter with no type hint ==")
loose = MCPServer("loose")


@loose.tool()
def lookup(key, limit: int = 5) -> str:
    """Look something up."""
    return str(key)


async def show_schema() -> None:
    async with Client(loose) as client:
        tool = (await client.list_tools()).tools[0]
        print("def lookup(key, limit: int = 5) publishes")
        print("    " + json.dumps(tool.input_schema["properties"]))


asyncio.run(show_schema())
