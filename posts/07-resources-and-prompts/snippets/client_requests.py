"""Post 07, sections 4, 5 and 8: the requests the SDK's client sends for completions, prompts and listening.

Run from the series root:
    uv run python posts/07-resources-and-prompts/snippets/client_requests.py

Connects an in-memory client to the project under code/05-first-server/ and
prints the method and params of three requests exactly as the client hands
them to its transport: completion/complete for the disk template, prompts/get
for diagnose_performance, and subscriptions/listen. The `_meta` envelope every
2026-07-28 request carries (post 03) is printed as its key list, and the
transport adds `jsonrpc` and `id` around what is shown.

To see the params, this script wraps the in-memory dispatcher's
send_raw_request, a private seam of mcp 2.3.0 (`client.session._dispatcher`);
it observes the call and changes nothing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402
from mcp_types import ResourceTemplateReference  # noqa: E402

from system_info import mcp  # noqa: E402


def show(method: str, params: dict) -> None:
    params = dict(params)
    meta = params.pop("_meta", {})
    print(f"\n== {method} ==")
    print(f"params._meta keys: {sorted(meta)}")
    print(json.dumps({"method": method, "params": params}, indent=2))


async def main() -> None:
    async with Client(mcp) as client:
        dispatcher = client.session._dispatcher
        send = dispatcher.send_raw_request

        async def observe(method, params, opts=None):
            show(method, params)
            return await send(method, params, opts)

        dispatcher.send_raw_request = observe

        await client.complete(
            ref=ResourceTemplateReference(uri="system://disk/{disk}"),
            argument={"name": "disk", "value": "r"},
        )
        await client.get_prompt("diagnose_performance", {"symptom": "high fan noise"})
        async with client.listen(
            resources_list_changed=True,
            resource_subscriptions=["system://processes/top"],
        ) as sub:
            print(f"\nacknowledged; subscription id {sub.subscription_id!r}")


asyncio.run(main())
