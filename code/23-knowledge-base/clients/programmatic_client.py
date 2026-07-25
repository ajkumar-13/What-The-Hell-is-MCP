"""The seventh client: no configuration file at all.

    python clients/programmatic_client.py                 # stdio, spawns the server
    python clients/programmatic_client.py --http          # connects to a running server
    python clients/programmatic_client.py --http --url http://127.0.0.1:8000/mcp

The six JSON files beside this one describe a server to a host that already
knows how to be an MCP client. This file *is* the client. It is the version you
reach for in continuous integration, in a chat bot, or in a batch job, and it is
also the fastest way to find out whether a server works before you spend twenty
minutes wondering why a desktop app shows a red dot and no error message.

## Three corrections to the older, widely copied version of this script

**1. `Client`, not `ClientSession` plus manual `initialize()`.** In SDK 2.x,
`mcp.Client` is the whole client. It owns the transport, the handshake, and the
multi-round-trip retry loop.

**2. `streamable_http_client(url)` is a transport, not a pair of streams.** The
v1 helper `streamablehttp_client()` yielded three values and was routinely
unpacked as two. Its v2 replacement yields a `TransportStreams` 2-tuple, and the
right thing to do with it is not to unpack it at all: hand the context manager
straight to `Client(...)` and let the client drive it. Every unpacking bug in
this area disappears if you never unpack.

**3. The endpoint path is `/mcp`, with no trailing slash.** That is the SDK's
`streamable_http_path` default. `/mcp/` is a different path and this server does
not serve it.

## What the run actually demonstrates

Capability probing. The script asks for tools, then *tries* resources and
prompts and carries on without them. That is exactly what a real host does, and
it is why the server is built the way it is: the tools alone answer the
question, and everything else is a nicer way to present the same corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from mcp import Client, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "http://127.0.0.1:8000/mcp"


def stdio_transport() -> object:
    """Spawn the server as a subprocess and talk to it over its stdin and stdout.

    `sys.executable` rather than a hard-coded `python`, so this works inside a
    virtual environment without depending on what `python` happens to mean on
    the caller's PATH. `cwd` is the project root so the package resolves and so
    the server finds `knowledge/` at its default location.
    """
    return stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=["-m", "knowledge_base"],
            cwd=str(PROJECT_ROOT),
            env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
        )
    )


def http_transport(url: str) -> object:
    """Connect to a server that is already running.

    Start one with `python -m knowledge_base --http`.

    For headers, authentication, or timeouts, build the HTTP client yourself and
    pass it in. Note that the SDK is on `httpx2`, not `httpx`: an `except
    httpx.ConnectError` left over from older code imports fine and silently
    stops catching anything.

        import httpx2
        http_client = httpx2.AsyncClient(headers={"Authorization": "Bearer ..."})
        transport = streamable_http_client(url, http_client=http_client)
    """
    return streamable_http_client(url)


async def probe(client: Client) -> None:
    """Drive the server the way a host would, and degrade where support is missing."""
    print(f"connected to: {client.server_info.name} {client.server_info.version}")
    print(f"protocol:     {client.protocol_version}")
    print()

    # ---- tools: the floor. Every MCP client has these. ---------------------
    tools = (await client.list_tools()).tools
    print(f"tools ({len(tools)}):")
    for tool in tools:
        read_only = tool.annotations.read_only_hint if tool.annotations else None
        print(f"  {tool.name:<14} read_only={read_only}")
    print()

    result = await client.call_tool("list_topics", {})
    topics = result.structured_content["topics"]
    print(f"handbook contains {len(topics)} document(s):")
    for topic in topics:
        print(f"  {topic['slug']:<14} {topic['title']}")
    print()

    question = "what do I do when the reconciler falls behind"
    hits = (
        await client.call_tool("search_docs", {"query": question, "limit": 3})
    ).structured_content
    print(f'search_docs("{question}") -> {hits["total_matches"]} match(es)')
    for hit in hits["results"]:
        print(f"  [{hit['score']:>6.2f}] {hit['title']} / {hit['heading']}")
        print(f"           {hit['excerpt'][:110]}")
    print()

    if hits["results"]:
        top = hits["results"][0]
        body = (
            await client.call_tool(
                "get_doc", {"slug": top["slug"], "section": top["anchor"]}
            )
        ).structured_content
        print(f"get_doc({top['slug']!r}, {top['anchor']!r}) -> {body['word_count']} words")
        print()

    # ---- resources: additive. Absence is not an error. --------------------
    try:
        resources = (await client.list_resources()).resources
    except MCPError as error:
        # A client that does not support resources never gets here, but a
        # *server* that does not publish them answers with an error, and a
        # well-behaved client keeps going. Everything above still worked.
        print(f"resources: unavailable ({error.message})")
    else:
        print(f"resources ({len(resources)}):")
        for resource in resources:
            print(f"  {resource.uri}")
    print()

    # ---- prompts: additive too. -------------------------------------------
    try:
        prompts = (await client.list_prompts()).prompts
    except MCPError as error:
        print(f"prompts: unavailable ({error.message})")
    else:
        print(f"prompts ({len(prompts)}):")
        for prompt in prompts:
            print(f"  {prompt.name:<20} {prompt.description or ''}")
    print()

    print("Everything above the resources line used tools only.")
    print("That is what this server looks like to the least capable host.")


async def main() -> None:
    parser = argparse.ArgumentParser(prog="programmatic_client")
    parser.add_argument(
        "--http",
        action="store_true",
        help="connect to a running server over Streamable HTTP instead of spawning one",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Streamable HTTP endpoint (default: {DEFAULT_URL})",
    )
    args = parser.parse_args()

    transport = http_transport(args.url) if args.http else stdio_transport()

    # One `Client`, either way. The transport is the only thing that changed,
    # which is the entire argument of post 23 expressed in one line of Python.
    async with Client(transport) as client:
        await probe(client)


if __name__ == "__main__":
    asyncio.run(main())
