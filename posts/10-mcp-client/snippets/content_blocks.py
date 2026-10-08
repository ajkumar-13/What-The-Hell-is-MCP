"""Post 10, section 6: every content block type, flattened for a model.

Run from the series root:
    uv run python posts/10-mcp-client/snippets/content_blocks.py

Builds one block of each of the five content types the specification
defines, plus one of a type no revision defines, and passes each through
describe_block from code/10-mcp-client/src/mcp_host/results.py. Prints the
kind and the rendering a host would put in front of a model, then shows what
ToolOutcome.for_model falls back to when a result has structuredContent and no
text block. Pure: no client, no server, no event loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))

from mcp_types import (  # noqa: E402
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from mcp_host.results import describe_block, read_result  # noqa: E402


class FutureContent:
    """A block type from a revision this client has never heard of."""

    type = "hologram"


BLOCKS = [
    TextContent(text="12 processes"),
    ImageContent(data="A" * 4096, mime_type="image/png"),
    AudioContent(data="B" * 1024, mime_type="audio/wav"),
    ResourceLink(name="top", uri="system://processes/top", mime_type="text/markdown"),
    EmbeddedResource(resource=TextResourceContents(uri="config://app", text="debug = false")),
    EmbeddedResource(
        resource=BlobResourceContents(uri="file:///x.bin", blob="Z" * 2048, mime_type="application/octet-stream")
    ),
    FutureContent(),
]


def main() -> None:
    for block in BLOCKS:
        described = describe_block(block)
        rendering = described.text.replace("\n", " | ")
        print(f"{type(block).__name__:<16} kind={described.kind:<14} {rendering}")

    image = describe_block(BLOCKS[1])
    print(f"\nthe image payload is still kept for the host: {len(image.data['data'])} characters")

    only_structured = CallToolResult(content=[], structured_content=[3, 1, 2])
    print(f"structuredContent and no text block: {read_result('sort', only_structured).for_model()}")


main()
