"""The audit trail: what changed, who asked, and whether it worked.

Post 14. Two rules make this useful rather than decorative:

1. **The successful-write row is inserted inside the write's own
   transaction.** If the write rolls back, the row rolls back with it, so the
   log never claims something happened that did not.
2. **Every audit write goes through the write pool.** An earlier version used
   the read-only pool for `ensure_audit_table()` and for the failure-path
   insert. The first meant the server could not start against a properly
   hardened read-only role; the second meant that failures, which are the
   entries you most want, were the ones that were never recorded.

The trail is exposed as a resource so a human can read it in the same
conversation the writes happened in. It is not exposed as a tool the model can
edit, and the write role should not hold `UPDATE` or `DELETE` on it.
"""

from __future__ import annotations

import logging
import os

import asyncpg

from .app import mcp
from .database import DatabaseNotConfigured, db

log = logging.getLogger("postgres-analyst.audit")

AUDIT_TABLE = "mcp_audit_log"

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
    id            BIGSERIAL PRIMARY KEY,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    operation     TEXT        NOT NULL,
    target_table  TEXT,
    sql_text      TEXT        NOT NULL,
    affected_rows INTEGER,
    success       BOOLEAN     NOT NULL,
    error_message TEXT,
    note          TEXT,
    session_id    TEXT        NOT NULL DEFAULT 'unknown',
    actor         TEXT        NOT NULL DEFAULT 'unknown'
)
"""

_CREATE_TIME_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS {AUDIT_TABLE}_occurred_at_idx
    ON {AUDIT_TABLE} (occurred_at DESC)
"""

_CREATE_OPERATION_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS {AUDIT_TABLE}_operation_idx
    ON {AUDIT_TABLE} (operation)
"""

_INSERT_SQL = f"""
INSERT INTO {AUDIT_TABLE}
    (operation, target_table, sql_text, affected_rows, success,
     error_message, note, session_id, actor)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
RETURNING id
"""

_RECENT_SQL = f"""
SELECT id, occurred_at, operation, target_table, sql_text,
       affected_rows, success, error_message, note
FROM {AUDIT_TABLE}
ORDER BY occurred_at DESC, id DESC
LIMIT $1
"""


def _identity() -> tuple[str, str]:
    """Who this process is acting as, for the audit row.

    These are deliberately not named `MCP_*`. The SDK's `Settings` object reads
    the whole `MCP_` prefix out of the environment, and putting unrelated keys
    there invites a collision that shows up as a confusing validation error at
    server construction.

    Neither value is an authenticated identity. Under stdio there is nobody to
    authenticate: the host launched this process. Post 20 covers what an
    identity looks like when there is a real one to record.
    """
    return (
        os.getenv("AUDIT_SESSION_ID", "unknown"),
        os.getenv("AUDIT_ACTOR", "unknown"),
    )


async def ensure_audit_table() -> None:
    """Create the audit table and its indexes if they are not there.

    Through the **write** pool. `CREATE TABLE` against a role carrying
    `default_transaction_read_only` fails with `cannot execute CREATE TABLE in
    a read-only transaction`, which, raised from inside a lifespan, presents to
    the user as a server that will not start and says nothing about why.
    """
    async with db.write_connection() as conn:
        # asyncpg does not reliably run several statements in one execute(),
        # so these go one at a time.
        await conn.execute(_CREATE_TABLE_SQL)
        await conn.execute(_CREATE_TIME_INDEX_SQL)
        await conn.execute(_CREATE_OPERATION_INDEX_SQL)
    log.info("audit table ready")


async def record_operation(
    *,
    operation: str,
    target_table: str,
    sql_text: str,
    success: bool,
    affected_rows: int | None = None,
    error_message: str | None = None,
    note: str | None = None,
    conn: asyncpg.Connection | None = None,
) -> int:
    """Insert one audit row and return its id.

    Pass `conn` to write inside a caller's transaction; that is how the
    success path stays atomic with the change it describes. Omit it and this
    takes a fresh connection from the **write** pool, which is what the
    failure path needs, because by then the caller's transaction has already
    rolled back.
    """
    session_id, actor = _identity()
    args = (
        operation,
        target_table,
        sql_text,
        affected_rows,
        success,
        error_message,
        note or None,
        session_id,
        actor,
    )

    if conn is not None:
        return await conn.fetchval(_INSERT_SQL, *args)

    async with db.write_connection() as fresh:
        return await fresh.fetchval(_INSERT_SQL, *args)


async def recent_operations(limit: int = 20) -> list[asyncpg.Record]:
    """The most recent audit rows, read through the read-only pool."""
    limit = max(1, min(int(limit), 200))
    async with db.read_connection() as conn:
        return await conn.fetch(_RECENT_SQL, limit)


def _cell(value: object) -> str:
    """Make a value safe to drop into a Markdown table cell.

    Recorded SQL is attacker-influenced text: whoever can talk to the model can
    get a statement recorded here, and an unescaped pipe would let them forge
    extra rows in this table.
    """
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_audit_markdown(rows: list[asyncpg.Record]) -> str:
    if not rows:
        return "No write operations have been recorded."

    lines = [
        "# Recent write operations",
        "",
        "| Time | Result | Operation | Table | Rows | Statement |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        when = row["occurred_at"].strftime("%Y-%m-%d %H:%M:%S")
        result = "ok" if row["success"] else "failed"
        rows_touched = "-" if row["affected_rows"] is None else row["affected_rows"]

        statement = row["sql_text"]
        if len(statement) > 80:
            statement = statement[:77] + "..."

        lines.append(
            f"| {when} | {result} | {_cell(row['operation'])} "
            f"| `{_cell(row['target_table'])}` "
            f"| {rows_touched} | `{_cell(statement)}` |"
        )

    failures = [r for r in rows if not r["success"]]
    if failures:
        lines.append("")
        lines.append("## Failures")
        lines.append("")
        for row in failures:
            message = (row["error_message"] or "").replace("\n", " ")
            lines.append(f"- id {row['id']}: {message}")

    return "\n".join(lines)


@mcp.resource(
    "postgres://audit",
    title="Write audit trail",
    description="The most recent write attempts, successful or not.",
    mime_type="text/markdown",
)
async def audit_resource() -> str:
    """The audit trail, as Markdown.

    A resource rather than a tool: this is something a human pins into the
    conversation to check the assistant's work, not something the assistant
    calls to decide what to do next.
    """
    try:
        rows = await recent_operations(20)
    except DatabaseNotConfigured as exc:
        return f"Audit trail unavailable: {exc}"
    except asyncpg.UndefinedTableError:
        return (
            "Audit trail unavailable: the audit table does not exist yet. It "
            "is created at startup when ENABLE_WRITES is true."
        )
    return render_audit_markdown(rows)
