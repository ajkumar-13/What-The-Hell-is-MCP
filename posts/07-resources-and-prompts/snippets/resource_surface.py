"""Post 07, sections 2, 6, 7 and 8: what the first server publishes beside its tools.

Run from the series root:
    uv run python posts/07-resources-and-prompts/snippets/resource_surface.py

Imports the project under code/05-first-server/ and connects an in-memory
client to it, as that project's tests/test_server.py does: no subprocess, no
socket, the real protocol path. Prints the capabilities the server declares,
the three list results in their wire spelling with their caching fields, what
a cursor changes (nothing), and the first line of each resource read.
"""

from __future__ import annotations

import asyncio
import json
import sys
from importlib.metadata import version
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402

from system_info import mcp  # noqa: E402


def wire(model) -> dict:
    """A result or object in its wire spelling (camelCase, unset fields left out)."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def caching(result) -> str:
    return f"ttlMs={result.ttl_ms} cacheScope={result.cache_scope!r} nextCursor={result.next_cursor!r}"


async def main() -> None:
    print(f"mcp {version('mcp')}, mcp-types {version('mcp-types')}, Python {sys.version.split()[0]}")

    async with Client(mcp) as client:
        caps = wire(client.server_capabilities)
        print("\n== capabilities in the server/discover result ==")
        for key in ("resources", "prompts", "completions"):
            print(f"{key}: {json.dumps(caps.get(key))}")

        resources = await client.list_resources()
        print("\n== resources/list ==")
        for item in resources.resources:
            print(json.dumps(wire(item)))
        print(caching(resources))

        templates = await client.list_resource_templates()
        print("\n== resources/templates/list ==")
        for item in templates.resource_templates:
            print(json.dumps(wire(item)))
        print(caching(templates))

        prompts = await client.list_prompts()
        print("\n== prompts/list ==")
        for item in prompts.prompts:
            print(json.dumps(wire(item)))
        print(caching(prompts))

        print("\n== resources/list with cursor 'page-2' ==")
        paged = await client.list_resources(cursor="page-2")
        print(f"{len(paged.resources)} resource(s); {caching(paged)}")

        print("\n== resources/read ==")
        for uri in ("system://processes/top", "system://disk/root"):
            read = await client.read_resource(uri)
            first = read.contents[0]
            print(f"{uri}: {len(read.contents)} content entry, mimeType {first.mime_type!r}, "
                  f"first line {first.text.splitlines()[0]!r}; ttlMs={read.ttl_ms} "
                  f"cacheScope={read.cache_scope!r}")


asyncio.run(main())
