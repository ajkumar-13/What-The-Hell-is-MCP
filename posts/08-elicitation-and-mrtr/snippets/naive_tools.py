"""Post 08, section 1: two ways not to gate a destructive tool, and what each publishes.

Run from the series root:
    uv run python posts/08-elicitation-and-mrtr/snippets/naive_tools.py

Registers the ungated tool and the tool with an approval flag on two
throwaway servers and prints the input schema each one publishes in
tools/list. Neither tool is ever called, so nothing is terminated.
"""

import asyncio
import json
import signal

import psutil
from mcp import Client
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("ungated")


@mcp.tool()
def terminate_process(pid: int) -> str:
    psutil.Process(pid).send_signal(signal.SIGTERM)
    return f"Sent SIGTERM to {pid}."


ungated = mcp
mcp = MCPServer("flagged")


@mcp.tool()
def terminate_process(pid: int, approved: bool) -> str:
    if not approved:
        return "Not approved."
    psutil.Process(pid).send_signal(signal.SIGTERM)
    return f"Sent SIGTERM to {pid}."


flagged = mcp


async def main() -> None:
    for server in (ungated, flagged):
        async with Client(server) as client:
            (tool,) = (await client.list_tools()).tools
        schema = tool.input_schema
        print(f"== {server.name}: terminate_process publishes ==")
        print(f"properties: {json.dumps(schema['properties'])}")
        print(f"required:   {json.dumps(schema['required'])}\n")


asyncio.run(main())
