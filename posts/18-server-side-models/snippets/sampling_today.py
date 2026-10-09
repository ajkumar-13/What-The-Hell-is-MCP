"""Post 18, sections 2 and 11: what happened to sampling, measured against the SDK.

Run from the series root:
    uv run python posts/18-server-side-models/snippets/sampling_today.py

Builds a throwaway server with two tools that want the host's model: one calls the
deprecated ctx.session.create_message() in its body, the old way; the other asks
through MRTR with the SDK's Resolve and Sample. It prints the deprecation marker the
SDK puts on the old call, then posts tools/call frames by hand to the server's
Streamable HTTP application at revision 2026-07-28 and prints what comes back. The
application runs in this process behind httpx's ASGI transport, so no port is opened,
no host is involved, and no model is called: the sampling request is only returned,
never answered.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import warnings
from typing import Annotated

import httpx
from mcp.server import _streamable_http_modern
from mcp.server.mcpserver import Context, MCPServer, Resolve, Sample
from mcp.server.session import ServerSession
from mcp_types import CreateMessageResult, SamplingMessage, TextContent

REVISION = "2026-07-28"
logging.disable(logging.WARNING)  # the server logs each refusal; the frames below show them

mcp = MCPServer("sampling-today")


@mcp.tool()
async def summarize_old_way(text: str, ctx: Context) -> str:
    """The pre-2026 shape: ask the client's model and wait for the answer."""
    reply = await ctx.session.create_message(
        [SamplingMessage(role="user", content=TextContent(type="text", text=text))],
        max_tokens=800,
    )
    return reply.content.text


def ask_host_model(text: str) -> Sample:
    return Sample(
        [SamplingMessage(role="user", content=TextContent(type="text", text=f"Summarize: {text}"))],
        max_tokens=800,
    )


@mcp.tool()
async def summarize_via_mrtr(text: str, summary: Annotated[CreateMessageResult, Resolve(ask_host_model)]) -> str:
    """The 2026-07-28 shape: return the sampling request inside an input_required result."""
    return summary.content.text


def frame(tool: str, capabilities: dict) -> dict:
    meta = {
        "io.modelcontextprotocol/protocolVersion": REVISION,
        "io.modelcontextprotocol/clientCapabilities": capabilities,
    }
    params = {"name": tool, "arguments": {"text": "..."}, "_meta": meta}
    return {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}


def headers(tool: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": REVISION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool,
    }


def shorten(body: dict) -> dict:
    result = body.get("result", {})
    if "requestState" in result:
        result["requestState"] = result["requestState"][:3] + "<sealed, shortened here>"
    return body


async def post(http: httpx.AsyncClient, tool: str, capabilities: dict) -> None:
    response = await http.post("/mcp", json=frame(tool, capabilities), headers=headers(tool))
    print(f"HTTP {response.status_code}")
    print(json.dumps(shorten(response.json()), indent=2))


async def main() -> None:
    print("== the old call site ==")
    print(f"ServerSession.create_message.__deprecated__: {ServerSession.create_message.__deprecated__!r}")
    fields = {f.name: f.default for f in dataclasses.fields(_streamable_http_modern._SingleExchangeDispatchContext)}
    print(f"2026-07-28 Streamable HTTP, per-request context: can_send_request defaults to {fields['can_send_request']}")

    app = mcp.streamable_http_app()
    async with mcp.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as http:
            print("\n== tools/call summarize_old_way, client declares sampling ==")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                await post(http, "summarize_old_way", {"sampling": {}})
            for w in caught:
                print(f"warning raised in the tool: {w.category.__name__}: {w.message}")

            print("\n== tools/call summarize_via_mrtr, client declares sampling ==")
            await post(http, "summarize_via_mrtr", {"sampling": {}})

            print("\n== tools/call summarize_via_mrtr, client declares nothing ==")
            await post(http, "summarize_via_mrtr", {})


asyncio.run(main())
