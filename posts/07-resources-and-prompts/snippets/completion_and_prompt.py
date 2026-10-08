"""Post 07, sections 4 and 8: the completion handler and the prompt, driven by a client.

Run from the series root:
    uv run python posts/07-resources-and-prompts/snippets/completion_and_prompt.py

Connects an in-memory client to the project under code/05-first-server/ and
prints the completion/complete results for a few prefixes, then the expanded
diagnose_performance prompt. Then, on a throwaway server with one required
prompt argument, prints what the SDK answers for a missing argument and an
unknown prompt name, and what the client does with a non-string argument.
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
from mcp_types import PromptReference, ResourceTemplateReference  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from system_info import mcp  # noqa: E402

# The in-memory dispatcher logs the traceback of each deliberate prompt failure
# below at ERROR on stderr; the client's view of it is what this script prints.
logging.getLogger("mcp.shared.direct_dispatcher").setLevel(logging.CRITICAL)


def wire(model) -> dict:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


async def main() -> None:
    async with Client(mcp) as client:
        print("== completion/complete ==")
        disk = ResourceTemplateReference(uri="system://disk/{disk}")
        for value in ("r", "zzz", ""):
            result = await client.complete(ref=disk, argument={"name": "disk", "value": value})
            print(f"ref/resource disk, value {value!r}: {json.dumps(wire(result.completion))}")
        symptom = PromptReference(name="diagnose_performance")
        for value in ("s", "H"):
            result = await client.complete(ref=symptom, argument={"name": "symptom", "value": value})
            print(f"ref/prompt symptom, value {value!r}: {json.dumps(wire(result.completion))}")
        result = await client.complete(ref=disk, argument={"name": "disk", "value": "r"})
        print("one full result:", json.dumps(wire(result)))

        print("\n== prompts/get diagnose_performance ==")
        prompt = await client.get_prompt("diagnose_performance", {"symptom": "high fan noise"})
        print(f"description: {prompt.description!r}; {len(prompt.messages)} message(s)")
        message = prompt.messages[0]
        print(f"role {message.role!r}, content type {message.content.type!r}")
        print(f"first line: {message.content.text.splitlines()[0]!r}")
        print(f"result carries ttlMs: {'ttlMs' in wire(prompt)}")

    print("\n== prompt failures, on a throwaway server ==")
    server = MCPServer("prompts")

    @server.prompt()
    def review(code: str) -> str:
        return f"Review this code:\n{code}"

    async with Client(server) as client:
        listed = (await client.list_prompts()).prompts[0]
        print(f"prompts/list: {json.dumps(wire(listed))}")
        for name, arguments in (("review", {}), ("no_such_prompt", {})):
            try:
                await client.get_prompt(name, arguments)
            except MCPError as exc:
                print(f"get_prompt({name!r}, {arguments}): error {exc.error.code} {exc.error.message!r}")
        try:
            await client.get_prompt("review", {"code": 42})
        except ValidationError as exc:
            first = exc.errors()[0]
            print(f"get_prompt('review', {{'code': 42}}): refused by the client before sending: "
                  f"{first['loc']} {first['msg']}")


asyncio.run(main())
