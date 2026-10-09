"""Post 13, section 9: what the analyst publishes, and how a row's values are coerced.

Run from the series root:
    uv run python posts/13-database-analyst/snippets/published_surface.py

Imports the project under code/13-postgres-analyst/ with DATABASE_URL empty and
connects an in-memory client to it, as the project's tests/test_server.py does.
For each tool it prints the input properties, the title and required fields of
the output schema, and the annotations in their wire spelling; then the
resource and template URIs. Last, it passes one value of each awkward type
through the project's jsonable() and prints what a row would carry.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "13-postgres-analyst"

import asyncio  # noqa: E402
import datetime as dt  # noqa: E402
import decimal  # noqa: E402
import ipaddress  # noqa: E402
import uuid  # noqa: E402

os.environ.update(DATABASE_URL="", DATABASE_WRITE_URL="", ENABLE_WRITES="false")
sys.path.insert(0, str(PROJECT / "src"))

from mcp import Client  # noqa: E402

from pg_analyst import mcp  # noqa: E402
from pg_analyst.query import jsonable  # noqa: E402


async def main() -> None:
    async with Client(mcp) as client:
        for tool in (await client.list_tools()).tools:
            out = tool.output_schema or {}
            hints = tool.annotations.model_dump(by_alias=True, exclude_none=True)
            hints.pop("title", None)
            print(f"tool {tool.name}")
            print(f"  input properties : {list(tool.input_schema['properties'])}")
            print(f"  output schema    : {out.get('title')}, required {out.get('required')}")
            print("  annotations      : " + " ".join(f"{k}={v}" for k, v in hints.items()))
        resources = sorted(str(r.uri) for r in (await client.list_resources()).resources)
        templates = [t.uri_template for t in (await client.list_resource_templates()).resource_templates]
        print(f"resources: {resources}")
        print(f"templates: {templates}")

    print("\njsonable(), one value of each type a row can carry:")
    for value in (
        decimal.Decimal("639.98"),
        dt.datetime(2026, 7, 24, 9, 30, tzinfo=dt.timezone.utc),
        dt.date(2026, 7, 24),
        dt.timedelta(days=2, hours=3),
        uuid.UUID("12345678-1234-5678-1234-567812345678"),
        ipaddress.ip_address("192.0.2.10"),
        b"\x89PNG\r\n\x1a\n" + bytes(8),
    ):
        print(f"  {type(value).__name__:<12} -> {jsonable(value)!r}")


asyncio.run(main())
