"""Post 10, sections 5 and 8: discovery, the cache hints, resources and prompts.

Run from the series root:
    uv run python posts/10-mcp-client/snippets/resources_prompts.py

Connects an in-memory client to the server under code/05-first-server/ and
prints, for each list method, the ttlMs and cacheScope the result carried;
which of the SDK's Client methods take a cache_mode argument; which result
types extend CacheableResult; one resource read, one prompt, and what asking
for a resource the server does not have raises.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

import mcp_types  # noqa: E402
from mcp import Client  # noqa: E402

from system_info import mcp  # noqa: E402

logging.disable(logging.INFO)  # the server logs the unknown resource at INFO on stderr


async def main() -> None:
    async with Client(mcp) as client:
        print("== cache hints on the list results ==")
        for method, call in [
            ("tools/list", client.list_tools),
            ("resources/list", client.list_resources),
            ("resources/templates/list", client.list_resource_templates),
            ("prompts/list", client.list_prompts),
        ]:
            result = await call()
            print(f"{method:<26} ttlMs={result.ttl_ms} cacheScope={result.cache_scope!r}")

        print("\n== which Client methods take cache_mode ==")
        for name in ["list_tools", "list_resources", "list_resource_templates", "list_prompts",
                     "read_resource", "call_tool", "get_prompt"]:
            takes = "cache_mode" in inspect.signature(getattr(Client, name)).parameters
            print(f"{name:<24} {takes}")

        print("\n== which results extend CacheableResult ==")
        for name in ["ListToolsResult", "ReadResourceResult", "CallToolResult", "GetPromptResult"]:
            print(f"{name:<20} {issubclass(getattr(mcp_types, name), mcp_types.CacheableResult)}")

        print("\n== a resource and a prompt ==")
        read = await client.read_resource("system://processes/top")
        print(f"resources/read system://processes/top: first line {read.contents[0].text.splitlines()[0]!r}")
        prompt = await client.get_prompt("diagnose_performance", {"symptom": "slow"})
        print(f"prompts/get diagnose_performance: {len(prompt.messages)} message(s), "
              f"role {prompt.messages[0].role!r}")

        print("\n== a resource the server does not have ==")
        try:
            await client.read_resource("system://does/not/exist")
        except Exception as exc:  # noqa: BLE001 - the point is to see what arrives
            print(repr(exc))


asyncio.run(main())
