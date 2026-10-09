"""Post 17, sections 7 and 8: a screenshot as an image block, and the alternatives.

Run from the series root:
    uv run python posts/17-research-browser/snippets/screenshots.py

Imports the package under code/17-research-browser/src, installs the suite's
`FixtureFetcher` from tests/conftest.py (its screenshot is a real 1x1 PNG, so no
browser is started), and connects an in-memory client. Prints the published
schema of `screenshot_page`, the blocks its result carries, and the structured
metadata beside them; then, on a throwaway server, what the previous edition's
`-> list` of dictionaries becomes; then a `resource_link` block built with the
SDK's own type, which the project does not return, to show the wire shape.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "17-research-browser"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

from conftest import ARTICLE_URL, FixtureFetcher  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp_types import ResourceLink  # noqa: E402

import research_browser  # noqa: E402
from research_browser.browser import browser  # noqa: E402

logging.disable(logging.CRITICAL)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def wire(block) -> str:
    return json.dumps(block.model_dump(by_alias=True, exclude_none=True))


async def the_real_tool() -> None:
    browser.use(FixtureFetcher())
    async with Client(research_browser.mcp) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "screenshot_page")
        result = await client.call_tool("screenshot_page", {"url": ARTICLE_URL})

    print("1. screenshot_page, as tools/list publishes it")
    schema = tool.output_schema
    print(f"   outputSchema: title={schema['title']}  properties={list(schema['properties'])}")
    print()
    print("2. one call")
    print(f"   content block types: {[block.type for block in result.content]}")
    print(f"   text block:  {wire(result.content[0])}")
    image = result.content[1]
    shown = image.model_copy(update={"data": image.data[:24] + "..."})
    print(f"   image block: {wire(shown)}")
    print(f"   data decodes to a PNG: {base64.b64decode(image.data).startswith(PNG_SIGNATURE)}; "
          f"base64 in the text block: {image.data in result.content[0].text}")
    print(f"   structuredContent: {json.dumps(result.structured_content)}")
    print(f"   pixels in structuredContent: {'data' in result.structured_content}")
    print()


async def the_old_defect() -> None:
    old = MCPServer("previous-edition")

    @old.tool()
    async def screenshot_page(url: str) -> list:
        return [
            {"type": "text", "text": f"Screenshot of {url} ..."},
            {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
        ]

    async with Client(old) as client:
        tool = (await client.list_tools()).tools[0]
        result = await client.call_tool("screenshot_page", {"url": ARTICLE_URL})
    print("3. the previous edition's return, -> list of dictionaries, on a throwaway server")
    print(f"   outputSchema: {json.dumps(tool.output_schema)}")
    print(f"   content block types: {[block.type for block in result.content]}")
    print(f"   second block's text: {json.dumps(result.content[1].text)}")
    print(f"   structuredContent: {json.dumps(result.structured_content)}")
    print()


def a_resource_link() -> None:
    link = ResourceLink(
        type="resource_link",
        uri="browser://page/what-a-page-costs",
        name="what-a-page-costs",
        mime_type="text/plain",
    )
    print("4. a resource_link block, built with mcp_types.ResourceLink (the project returns none)")
    print(f"   {wire(link)}")


async def main() -> None:
    await the_real_tool()
    await the_old_defect()
    a_resource_link()


if __name__ == "__main__":
    asyncio.run(main())
