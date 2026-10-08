"""Post 07, sections 5 and 6: subscriptions/listen and the caching fields, end to end.

Run from the series root:
    uv run python posts/07-resources-and-prompts/snippets/listen_and_cache.py

Builds a throwaway server with one static resource and one tool that changes
it, and connects an in-memory client. The client opens a subscriptions/listen
stream naming the resource, calls the tool, prints the one event that arrives
(and that the list-change notification it did not ask for does not), and
re-reads. Then prints what a server built with subscriptions=False declares
and answers, what a pre-2026 client's resources/subscribe call gets over a
legacy initialize connection, and the ttlMs and
cacheScope that MCPServer(cache_hints=...) puts on the results.
"""

from __future__ import annotations

import asyncio
import json
import warnings

from mcp import Client, MCPError
from mcp.server import CacheHint
from mcp.server.mcpserver import Context, MCPServer

mcp = MCPServer("notes")
NOTE = {"text": "first draft"}


@mcp.resource("notes://today", mime_type="text/plain")
def today() -> str:
    """Today's note."""
    return NOTE["text"]


@mcp.tool()
async def rewrite_note(text: str, ctx: Context) -> str:
    """Replace today's note, then tell every listener that asked about it."""
    NOTE["text"] = text
    await ctx.notify_resource_updated("notes://today")
    await ctx.notify_resources_changed()
    return "rewritten"


async def watch(client: Client) -> None:
    async with client.listen(
        tools_list_changed=True,
        resource_subscriptions=["notes://today"],
    ) as sub:
        print(f"subscription id {sub.subscription_id!r}, honored "
              f"{json.dumps(sub.honored.model_dump(mode='json', by_alias=True, exclude_none=True))}")
        await client.call_tool("rewrite_note", {"text": "second draft"})
        event = await asyncio.wait_for(sub.__anext__(), timeout=5)
        print(f"event: {event!r}")
        try:
            extra = await asyncio.wait_for(sub.__anext__(), timeout=0.5)
            print(f"second event: {extra!r}")
        except TimeoutError:
            print("no second event within 0.5 s (resources list_changed was not requested)")
        read = await client.read_resource("notes://today")
        print(f"re-read: {read.contents[0].text!r}")


async def main() -> None:
    print("== subscriptions/listen ==")
    async with Client(mcp) as client:
        caps = client.server_capabilities.resources
        print(f"resources capability: subscribe={caps.subscribe} listChanged={caps.list_changed}")
        await watch(client)

    print("\n== a pre-2026 resources/subscribe call ==")
    # mode="legacy" connects with the initialize handshake, as a pre-2026 client does;
    # on a 2026-07-28 connection the method does not exist for any server.
    async with Client(mcp, mode="legacy") as client:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # the client marks the method deprecated
            try:
                await client.subscribe_resource("notes://today")
            except MCPError as exc:
                print(f"error {exc.error.code} {exc.error.message!r}")

    print("\n== MCPServer(subscriptions=False) ==")
    quiet = MCPServer("quiet", subscriptions=False)
    quiet.resource("notes://today")(today)
    async with Client(quiet) as client:
        caps = client.server_capabilities.resources
        print(f"resources capability: subscribe={caps.subscribe} listChanged={caps.list_changed}")
        try:
            async with client.listen(resource_subscriptions=["notes://today"]):
                pass
        except MCPError as exc:
            print(f"subscriptions/listen: error {exc.error.code} {exc.error.message!r}")

    print("\n== caching fields ==")
    async with Client(mcp) as client:
        listed = await client.list_resources()
        read = await client.read_resource("notes://today")
        print(f"default:     resources/list ttlMs={listed.ttl_ms} {listed.cache_scope!r}; "
              f"resources/read ttlMs={read.ttl_ms} {read.cache_scope!r}")
    hinted = MCPServer("hinted", cache_hints={"resources/read": CacheHint(ttl_ms=60_000, scope="public")})
    hinted.resource("notes://today")(today)
    async with Client(hinted) as client:
        listed = await client.list_resources()
        read = await client.read_resource("notes://today")
        print(f"cache_hints: resources/list ttlMs={listed.ttl_ms} {listed.cache_scope!r}; "
              f"resources/read ttlMs={read.ttl_ms} {read.cache_scope!r}")
    try:
        MCPServer("wrong", cache_hints={"prompts/get": CacheHint(ttl_ms=1)})
    except ValueError as exc:
        print(f"cache_hints for prompts/get: ValueError: {exc}")


asyncio.run(main())
