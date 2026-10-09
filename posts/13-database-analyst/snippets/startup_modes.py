"""Post 13, section 3: a missing DATABASE_URL against a wrong one.

Run from the series root:
    uv run python posts/13-database-analyst/snippets/startup_modes.py

Imports the project under code/13-postgres-analyst/ and connects an in-memory
client to it, as the project's tests/test_server.py does. Twice:

1. With DATABASE_URL empty. Prints the project's own log lines from startup
   (the format is the project's, without the timestamp), the result of one
   query_database call, and the text of the postgres://schema resource.
2. With DATABASE_URL naming a port on 127.0.0.1 that nothing listens on,
   set after the import. Prints what opening the client raises.

Nothing here needs PostgreSQL. The series root's environment pins the
project's dependencies too; should it lack them, the script runs itself again
under `uv run --project code/13-postgres-analyst`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "13-postgres-analyst"

try:
    import asyncpg  # noqa: F401  the project's own dependencies
    import dotenv  # noqa: F401
    import sqlglot  # noqa: F401
except ModuleNotFoundError:
    if os.environ.get("POST13_SNIPPET_CHILD"):
        raise
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env.update(POST13_SNIPPET_CHILD="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    uv = os.environ.get("UV", "uv")
    sys.exit(subprocess.call(
        [uv, "run", "--frozen", "--quiet", "--project", str(PROJECT), "python", str(Path(__file__).resolve())],
        env=env,
    ))

import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import socket  # noqa: E402
import traceback  # noqa: E402

# An empty value is "not set" to the project, and load_dotenv() never overrides
# a variable that already exists, so a .env on this machine changes nothing.
os.environ.update(DATABASE_URL="", DATABASE_WRITE_URL="", ENABLE_WRITES="false")
sys.path.insert(0, str(PROJECT / "src"))

from mcp import Client  # noqa: E402

from pg_analyst import mcp  # noqa: E402


class Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main() -> None:
    collect = Collect()
    logging.getLogger("postgres-analyst").addHandler(collect)

    print("== 1. DATABASE_URL is not set ==")
    async with Client(mcp) as client:
        print("startup log:")
        for line in collect.lines:
            print(f"  {line}")
        result = await client.call_tool("query_database", {"sql": "SELECT email FROM users LIMIT 5"})
        print(f"\nquery_database: resultType {result.result_type!r}, isError {json.dumps(result.is_error)}")
        print(json.dumps(result.structured_content, indent=2))
        schema = (await client.read_resource("postgres://schema")).contents[0].text
        print(f"\npostgres://schema reads: {schema}")

    collect.lines.clear()
    os.environ["DATABASE_URL"] = f"postgresql://mcp_readonly@127.0.0.1:{free_port()}/analytics"
    print("\n== 2. DATABASE_URL names a port nothing listens on ==")
    try:
        async with Client(mcp):
            print("the client opened, which was not expected")
    except OSError as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        ours = [f"{Path(f.filename).name}:{f.name}" for f in frames if "pg_analyst" in f.filename]
        print(f"opening the client raised {type(exc).__name__}, raised through {' -> '.join(ours)}")
    print(f"log lines from the project before it failed: {len(collect.lines)}")


asyncio.run(main())
