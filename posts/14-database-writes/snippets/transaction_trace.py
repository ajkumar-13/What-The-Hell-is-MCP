"""Post 14, sections 2, 5, 6, 7 and 9: the write path's transactions, recorded, and the audit trail it leaves.

Run from the series root:
    uv run python posts/14-database-writes/snippets/transaction_trace.py

No PostgreSQL is involved. A recording stub stands in for the two asyncpg
pools of code/13-postgres-analyst/: it writes down every acquire, BEGIN,
execute, fetchval, COMMIT and ROLLBACK in order, answers each statement with
the command tag a freshly seeded database returns for it, raises the errors a
scenario asks for, keeps the audit rows in a list, and discards the rows a
rolled-back transaction inserted. The project's code is imported unmodified
and driven through an in-memory client whose user approves every write.

It prints: the startup trace with writes enabled; a committed write; a write
whose statement fails; the same failure with the failure path pointed at the
read pool; a write whose own audit insert fails; one UPDATE answered four ways
and the audit resource read back afterwards; how the resource renders a
statement carrying a pipe and a newline; and what MCPServer does with an
MCP_-prefixed variable in the environment.

The validator and the driver live in the project's environment rather than the
root one, so when the root environment lacks the project's dependencies the
script runs itself again under `uv run --project code/13-postgres-analyst`.
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
    if os.environ.get("POST14_SNIPPET_CHILD"):
        raise
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env.update(POST14_SNIPPET_CHILD="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    uv = os.environ.get("UV", "uv")
    sys.exit(subprocess.call(
        [uv, "run", "--frozen", "--quiet", "--project", str(PROJECT), "python", str(Path(__file__).resolve())],
        env=env,
    ))

import asyncio  # noqa: E402
import datetime as dt  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

os.environ.update(DATABASE_URL="", DATABASE_WRITE_URL="", ENABLE_WRITES="true")
os.environ.pop("AUDIT_SESSION_ID", None)
os.environ.pop("AUDIT_ACTOR", None)
sys.path.insert(0, str(PROJECT / "src"))

from mcp import Client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from pg_analyst import mcp  # noqa: E402
from pg_analyst.database import db  # noqa: E402

logging.getLogger().handlers[0].setLevel(logging.CRITICAL)  # keep stderr quiet


class Errors(logging.Handler):
    """Collects the project's ERROR log lines so the script can print them."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")


errors = Errors()
logging.getLogger("postgres-analyst").addHandler(errors)

# The tags PostgreSQL returns for these statements against sql/001-seed.sql.
TAGS = {
    "DELETE FROM orders WHERE status = 'cancelled'": "DELETE 1",
    "UPDATE users SET active = FALSE WHERE id = 3": "UPDATE 1",
}
CLOCK = dt.datetime(2026, 10, 9, 12, 0, 0, tzinfo=dt.timezone.utc)  # the stub's clock


class Store:
    """The stub's database: the audit rows, the trace, and the failures to inject."""

    def __init__(self, fail_statement: str | None = None, fail_audit: bool = False) -> None:
        self.rows: list[dict] = []
        self.trace: list[str] = []
        self.next_id = 1
        self.fail_statement = fail_statement
        self.fail_audit = fail_audit

    def step(self, text: str) -> None:
        self.trace.append(text)

    def show(self) -> None:
        for n, line in enumerate(self.trace, 1):
            print(f"{n}. {line}")
        self.trace.clear()


class Transaction:
    def __init__(self, conn: "Connection") -> None:
        self.conn = conn

    async def __aenter__(self):
        self.conn.store.step("BEGIN")
        self.conn.pending = []
        return self

    async def __aexit__(self, exc_type, exc, tb):
        store = self.conn.store
        if exc_type is None:
            store.step("COMMIT")
            store.rows.extend(self.conn.pending)
        else:
            store.step("ROLLBACK")  # the rows this transaction inserted go with it
        self.conn.pending = None
        return False


class Connection:
    def __init__(self, store: Store, read_only: bool) -> None:
        self.store = store
        self.read_only = read_only
        self.pending: list[dict] | None = None

    def transaction(self) -> Transaction:
        return Transaction(self)

    async def execute(self, sql: str, *args) -> str:
        text = " ".join(sql.split())
        if text.startswith("CREATE"):
            self.store.step(f"execute({text[:48]}...)")
            return "CREATE TABLE"
        if text == self.store.fail_statement:
            self.store.step(f'execute("{text}") raises InsufficientPrivilegeError')
            raise asyncpg.InsufficientPrivilegeError("permission denied for table orders")
        tag = TAGS[text]
        self.store.step(f'execute("{text}") returns {tag!r}')
        return tag

    async def fetchval(self, sql: str, *args) -> int:
        success = args[4]
        if self.read_only:
            self.store.step(f"fetchval(audit insert, success={success}) raises ReadOnlySQLTransactionError")
            raise asyncpg.ReadOnlySQLTransactionError("cannot execute INSERT in a read-only transaction")
        if self.store.fail_audit:
            self.store.step(f"fetchval(audit insert, success={success}) raises InsufficientPrivilegeError")
            raise asyncpg.InsufficientPrivilegeError("permission denied for table mcp_audit_log")
        row_id = self.store.next_id
        self.store.next_id += 1
        names = ("operation", "target_table", "sql_text", "affected_rows", "success",
                 "error_message", "note", "session_id", "actor")
        row = dict(zip(names, args), id=row_id, occurred_at=CLOCK + dt.timedelta(seconds=row_id))
        (self.pending if self.pending is not None else self.store.rows).append(row)
        self.store.step(f"fetchval(audit insert, success={success}) returns id {row_id}")
        return row_id

    async def fetch(self, sql: str, limit: int) -> list[dict]:
        self.store.step("fetch(recent audit rows)")
        return sorted(self.store.rows, key=lambda r: (r["occurred_at"], r["id"]), reverse=True)[:limit]


class Pool:
    def __init__(self, store: Store, name: str) -> None:
        self.store = store
        self.name = name

    @asynccontextmanager
    async def acquire(self):
        self.store.step(f"acquire: {self.name} pool")
        yield Connection(self.store, read_only=self.name == "read")

    async def close(self) -> None:
        pass


def install(store: Store) -> None:
    """Hand the stub pools to the project's Database before each client starts.

    The lifespan closes the pools when a client disconnects, so every call
    installs them again.
    """
    db._read_pool = Pool(store, "read")
    db._write_pool = Pool(store, "write")


def user(action: str, content: dict | None = None):
    async def callback(context, params):
        return ElicitResult(action=action, content=content)

    return callback


async def write(store: Store, sql: str, action: str = "accept", content: dict | None = None) -> dict:
    install(store)
    if action == "accept" and content is None:
        content = {"confirm": True}
    async with Client(mcp, elicitation_callback=user(action, content)) as client:
        store.trace.clear()  # drop the startup steps
        result = await client.call_tool("write_database", {"sql": sql})
    return result.structured_content


def show_result(data: dict) -> None:
    print(json.dumps(data))


def show_errors() -> None:
    for line in errors.lines:
        print(f"log: {line}")
    errors.lines.clear()


async def main() -> None:
    store = Store()
    install(store)
    print("== startup with ENABLE_WRITES=true and a write pool ==")
    async with Client(mcp):
        pass
    store.show()

    print("\n== a committed write ==")
    data = await write(store, "DELETE FROM orders WHERE status = 'cancelled'")
    store.show()
    show_result(data)

    print("\n== the statement fails ==")
    failing = Store(fail_statement="DELETE FROM orders WHERE status = 'cancelled'")
    data = await write(failing, "DELETE FROM orders WHERE status = 'cancelled'")
    failing.show()
    show_result(data)
    print(f"audit rows kept: {[(r['id'], r['success']) for r in failing.rows]}")

    print("\n== the same failure, with the failure path given the read pool ==")
    misrouted = Store(fail_statement="DELETE FROM orders WHERE status = 'cancelled'")
    db.write_connection = db.read_connection  # the first edition's mistake, on this instance only
    try:
        data = await write(misrouted, "DELETE FROM orders WHERE status = 'cancelled'")
    finally:
        del db.write_connection
    misrouted.show()
    show_result(data)
    print(f"audit rows kept: {[(r['id'], r['success']) for r in misrouted.rows]}")
    show_errors()

    print("\n== the audit insert itself fails, inside the transaction ==")
    audit_fails = Store(fail_audit=True)
    data = await write(audit_fails, "UPDATE users SET active = FALSE WHERE id = 3")
    audit_fails.show()
    show_result(data)
    print(f"audit rows kept: {[(r['id'], r['success']) for r in audit_fails.rows]}")
    show_errors()

    print("\n== one UPDATE, four answers, then the audit resource ==")
    lab = Store()
    for action, content in (("accept", {"confirm": True}), ("decline", None),
                            ("cancel", None), ("accept", {"confirm": False})):
        data = await write(lab, "UPDATE users SET active = FALSE WHERE id = 3", action, content)
        label = f"{action} {json.dumps(content)}" if content else action
        print(f"{label:<26} status={data['status']!r} audit_id={data['audit_id']}")
    install(lab)
    async with Client(mcp) as client:
        text = (await client.read_resource("postgres://audit")).contents[0].text
    print(text)
    row = lab.rows[0]
    print(f"row 1: session_id={row['session_id']!r} actor={row['actor']!r} note={row['note']!r}")

    print("\n== a recorded statement with a pipe and a newline, as the resource renders it ==")
    from pg_analyst.audit import render_audit_markdown

    hostile = {"id": 9, "occurred_at": CLOCK, "operation": "UPDATE", "target_table": "products",
               "sql_text": "UPDATE products SET name = 'a | ok |\n| forged' WHERE id = 8",
               "affected_rows": 1, "success": True, "error_message": None}
    print(render_audit_markdown([hostile]).splitlines()[-1])

    print("\n== an MCP_ variable in the environment ==")
    os.environ["MCP_DEBUG"] = "not-a-bool"
    server = MCPServer("probe")
    print(f"MCPServer('probe') with MCP_DEBUG=not-a-bool: constructed, settings.debug={server.settings.debug}")
    del os.environ["MCP_DEBUG"]


asyncio.run(main())
