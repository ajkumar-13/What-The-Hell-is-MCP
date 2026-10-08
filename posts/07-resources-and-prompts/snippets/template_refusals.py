"""Post 07, section 3: four hostile reads of a templated resource, and where each one stops.

Run from the series root:
    uv run python posts/07-resources-and-prompts/snippets/template_refusals.py

Part 1 connects an in-memory client to the project under code/05-first-server/
and reads system://disk/{disk} with four values, printing the JSON-RPC error
each read gets. Part 2 asks the SDK's two layers that run before the handler,
the URI template matcher and the ResourceSecurity policy, what they make of
the same URIs. Part 3 widens the template to {+disk} on a throwaway server,
first with the default policy and then with the policy switched off for that
parameter, and prints what the handler receives. Part 4 lets the first refusal
escape the client's async with block and prints the type that arrives.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client, MCPError  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.resources import ResourceSecurity  # noqa: E402
from mcp.shared.uri_template import UriTemplate  # noqa: E402

from system_info import mcp  # noqa: E402

# The server logs each refused read at INFO on stderr; keep stdout for the results.
logging.getLogger("mcp.server.mcpserver.server").setLevel(logging.WARNING)

HOSTILE = [
    "system://disk/etc",
    "system://disk/../../etc/passwd",
    "system://disk/..%2F..%2Fetc%2Fpasswd",
    "system://disk/..",
]


async def part1() -> None:
    print("== 1. resources/read against the first server ==")
    async with Client(mcp) as client:
        for uri in HOSTILE:
            try:
                await client.read_resource(uri)
                print(f"{uri}\n    read succeeded")
            except MCPError as exc:
                print(f"{uri}\n    error {json.dumps(exc.error.model_dump(mode='json', exclude_none=True))}")


def part2() -> None:
    print("\n== 2. the two SDK layers in front of the handler ==")
    template = UriTemplate.parse("system://disk/{disk}")
    policy = ResourceSecurity()  # the default MCPServer applies to every template
    for uri in HOSTILE:
        params = template.match(uri)
        if params is None:
            print(f"{uri}\n    matcher: no match")
            continue
        failed = policy.validate(params)
        verdict = "passes" if failed is None else f"rejects {failed!r}"
        print(f"{uri}\n    matcher: {params}; ResourceSecurity: {verdict}")


async def part3() -> None:
    print("\n== 3. the template widened to {+disk} ==")
    for label, security in (("default policy", None),
                            ("policy off for disk", ResourceSecurity(exempt_params={"disk"}))):
        server = MCPServer("widened")
        options = {} if security is None else {"security": security}

        @server.resource("system://disk/{+disk}", **options)
        def disk_usage(disk: str) -> str:
            return f"handler entered with disk={disk!r}"

        async with Client(server) as client:
            uri = "system://disk/../../etc/passwd"
            try:
                text = (await client.read_resource(uri)).contents[0].text
            except MCPError as exc:
                text = f"error {exc.error.code}: {exc.error.message}"
            print(f"{label}: {text}")


async def part4() -> None:
    print("\n== 4. the same refusal, caught outside the client's async with ==")
    try:
        async with Client(mcp) as client:
            await client.read_resource("system://disk/etc")
    except MCPError:
        print("caught as MCPError")
    except BaseException as exc:  # what actually arrives once the block has unwound
        inner, depth = exc, 0
        while isinstance(inner, BaseExceptionGroup):
            inner, depth = inner.exceptions[0], depth + 1
        print(f"caught as {type(exc).__name__}; {depth} group level(s) down: "
              f"{type(inner).__name__} {inner.error.code} {inner.error.message!r}")


async def main() -> None:
    await part1()
    part2()
    await part3()
    await part4()


asyncio.run(main())
