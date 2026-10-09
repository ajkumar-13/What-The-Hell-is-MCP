"""Post 13, sections 5 and 7: one refused tools/call, as it crosses the wire.

Run from the series root:
    uv run python posts/13-database-analyst/snippets/refusal_on_the_wire.py

Imports the project under code/13-postgres-analyst/ with DATABASE_URL empty and
connects an in-memory client to it, as the project's tests/test_server.py does.
It sends query_database a data-modifying common table expression and prints the
method and params the client hands to its transport, then the result as it came
back: resultType, isError, structuredContent, and the result's _meta. The
transport adds `jsonrpc` and `id` around what is shown. Then it sends the four
statements of section 7 that sqlglot cannot model and prints the error each
result carries.

To see the frames, the script wraps the in-memory dispatcher's
send_raw_request, a private seam of mcp 2.3.0 (`client.session._dispatcher`);
it observes the call and changes nothing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "13-postgres-analyst"

import asyncio  # noqa: E402
import json  # noqa: E402

os.environ.update(DATABASE_URL="", DATABASE_WRITE_URL="", ENABLE_WRITES="false")
sys.path.insert(0, str(PROJECT / "src"))

from mcp import Client  # noqa: E402

from pg_analyst import mcp  # noqa: E402

CTE = "WITH doomed AS (DELETE FROM users RETURNING *) SELECT * FROM doomed LIMIT 1"
OPAQUE = (
    "VACUUM FULL users",
    "DO $$ BEGIN PERFORM pg_sleep(30); END $$",
    "CALL do_it()",
    "EXPLAIN ANALYZE DELETE FROM users",
)


async def main() -> None:
    async with Client(mcp) as client:
        dispatcher = client.session._dispatcher
        send = dispatcher.send_raw_request
        seen: list[tuple[str, dict, dict]] = []

        async def observe(method, params, opts=None):
            result = await send(method, params, opts)
            seen.append((method, params, result))
            return result

        dispatcher.send_raw_request = observe
        await client.call_tool("query_database", {"sql": CTE})
        dispatcher.send_raw_request = send

        method, params, result = next(s for s in seen if s[0] == "tools/call")
        print("== the request ==")
        print(json.dumps({"method": method, "params": params}, indent=2))
        print("\n== the result ==")
        print(f"resultType: {json.dumps(result['resultType'])}")
        print(f"isError: {json.dumps(result.get('isError'))}")
        print(f"_meta: {json.dumps(result.get('_meta'))}")
        print("structuredContent:")
        print(json.dumps(result["structuredContent"], indent=2))
        same = json.loads(result["content"][0]["text"]) == result["structuredContent"]
        print(f"content[0] is one text block carrying the same object: {same}")

        print("\n== section 7: four statements sqlglot cannot model, sent to query_database ==")
        errors = set()
        for sql in OPAQUE:
            r = await client.call_tool("query_database", {"sql": sql})
            data = r.structured_content
            errors.add(data["error"])
            print(f"{sql!r}: ok={json.dumps(data['ok'])} executed_sql={data['executed_sql']!r}")
        print(f"distinct error strings: {len(errors)}")
        for error in sorted(errors):
            print(f"error: {error}")


asyncio.run(main())
