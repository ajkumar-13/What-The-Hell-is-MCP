"""Post 07, sections 2, 3 and 10: what @mcp.resource accepts, and what it refuses at registration.

Run from the series root:
    uv run python posts/07-resources-and-prompts/snippets/registration_errors.py

Registers the three working URI shapes on a throwaway MCPServer and prints how
the SDK classified each one (static resource or template). Then registers six
broken resources, each on a fresh server, and prints the exception each one
raises when the decorator runs, before any client has connected.
"""

from __future__ import annotations

import asyncio

from mcp import Client
from mcp.server.mcpserver import Context, MCPServer

mcp = MCPServer("shapes")


@mcp.resource("config://app")                        # static
def config() -> str: ...

@mcp.resource("weather://{city}/current")            # one variable
def weather(city: str) -> str: ...

@mcp.resource("notes://{name}{?limit}")              # query expansion
def notes(name: str, limit: int = 10) -> str: ...


def static_with_parameter(server: MCPServer) -> None:
    @server.resource("static://y")
    def handler(a: str) -> str: ...


def static_with_context(server: MCPServer) -> None:
    @server.resource("static://x")
    def handler(ctx: Context) -> str: ...


def names_disagree(server: MCPServer) -> None:
    @server.resource("weather://{city}/current")
    def handler(town: str) -> str: ...


def query_without_default(server: MCPServer) -> None:
    @server.resource("notes://{name}{?b}")
    def handler(name: str, b: int) -> str: ...


def adjacent_variables(server: MCPServer) -> None:
    @server.resource("pair://{a}{b}")
    def handler(a: str, b: str) -> str: ...


def no_parentheses(server: MCPServer) -> None:
    @server.resource
    def handler() -> str: ...


async def main() -> None:
    print("== three working shapes ==")
    async with Client(mcp) as client:
        static = [r.uri for r in (await client.list_resources()).resources]
        templated = [t.uri_template for t in (await client.list_resource_templates()).resource_templates]
    print(f"resources/list carries {static}")
    print(f"resources/templates/list carries {templated}")

    print("\n== six broken registrations ==")
    for attempt in (static_with_parameter, static_with_context, names_disagree,
                    query_without_default, adjacent_variables, no_parentheses):
        try:
            attempt(MCPServer("broken"))
            print(f"{attempt.__name__}: registered")
        except Exception as exc:  # each one raises while the decorator runs
            print(f"{attempt.__name__}:\n    {type(exc).__name__}: {exc}")


asyncio.run(main())
