"""Post 06, section 6: content blocks.

Run from the series root:
    uv run python posts/06-tools-in-depth/snippets/content_blocks.py

Prints the five members of the protocol's ContentBlock union as the installed
mcp_types package defines them, the image block the SDK's Image helper
produces for a one-pixel PNG, and the result of a tool that returns a short
text block beside a resource link. Nothing is served over a transport and
nothing is left running.
"""

from __future__ import annotations

import asyncio
import json
import struct
import typing
import zlib

import mcp_types
from mcp import Client
from mcp.server.mcpserver import Image, MCPServer
from mcp_types import CallToolResult, ResourceLink, TextContent

mcp = MCPServer("content-demo")


def one_pixel_png() -> bytes:
    """A valid 1x1 grey PNG, built with the standard library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x80")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


@mcp.tool()
def picture() -> Image:
    """Return a one-pixel image through the SDK helper."""
    return Image(data=one_pixel_png(), format="png")


@mcp.tool()
def multi() -> CallToolResult:
    """Return several content blocks at once."""
    return CallToolResult(
        content=[
            TextContent(type="text", text="Rendered 1 chart and saved the raw data."),
            ResourceLink(
                type="resource_link",
                uri="file:///tmp/report.csv",
                name="report.csv",
                description="Full result set, 12000 rows",
                mime_type="text/csv",
            ),
        ],
        structured_content={"rows": 12000},
    )


def wire(result) -> dict:
    data = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    data.pop("_meta", None)  # serverInfo, the same on every result
    return data


async def main() -> None:
    members = " | ".join(t.__name__ for t in typing.get_args(mcp_types.ContentBlock))
    print(f"ContentBlock = {members}")

    async with Client(mcp) as client:
        block = wire(await client.call_tool("picture", {}))["content"][0]
        print("\n== the Image helper's block, data shortened to 24 characters ==")
        block["data"] = block["data"][:24] + "..."
        print(json.dumps(block))

        print("\n== tools/call multi ==")
        print(json.dumps(wire(await client.call_tool("multi", {})), indent=2))


asyncio.run(main())
