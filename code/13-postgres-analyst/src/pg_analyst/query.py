"""The read path: two tools, both read-only, both structured.

Post 13. The pipeline for `query_database` is the whole architecture in one
function:

    validate  ->  regenerate  ->  read-only pool  ->  structure  ->  return

Validation happens before anything is acquired from a pool, so a rejected
statement never gets near a connection. What runs is the SQL regenerated from
the parse tree, not the text that arrived, and the caller is told which is
which by getting `executed_sql` back in the result.

Errors are returned in the result rather than raised. A model that gets a
structured `ok: false` with a `error` string can correct itself on the next
turn; a model that gets a protocol error usually just apologizes.
"""

from __future__ import annotations

import datetime as dt
import decimal
import ipaddress
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from mcp_types import ToolAnnotations

from .app import mcp
from .database import DatabaseNotConfigured, db
from .schema import MAX_SAMPLE_ROWS, fetch_table_sample
from .security import MAX_ROWS, SecurityError, validate_read_query

log = logging.getLogger("postgres-analyst.query")

#: Both tools in this module genuinely do not write. The annotation is a hint
#: to the host, not enforcement; the enforcement is the read-only pool these
#: functions acquire from and the read-only role behind it.
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

#: Values PostgreSQL hands back that JSON has no opinion about. Anything not in
#: the JSON primitive set is stringified rather than dropped, so a timestamp
#: column still arrives as something the model can read.
_JSON_PRIMITIVES = (str, int, float, bool, type(None))


def jsonable(value: Any) -> Any:
    """Coerce one column value into something JSON can carry."""
    if isinstance(value, _JSON_PRIMITIVES):
        return value
    if isinstance(value, decimal.Decimal):
        # str, not float. A NUMERIC column is exact and float is not, and
        # silently losing precision on a money column is a bad way to find out.
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, (uuid.UUID, ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)


def rows_to_dicts(rows: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [{key: jsonable(value) for key, value in row.items()} for row in rows]


@dataclass
class QueryResult:
    """The result of a read.

    The class-body annotations are load-bearing. A class that only assigns
    attributes in `__init__` has no type hints for the SDK to read, and the
    tool ships with `outputSchema: null` and no warning at all.
    """

    ok: bool
    executed_sql: str
    row_count: int
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    notice: str = ""
    error: str = ""


@dataclass
class TableSample:
    """A handful of rows from one table."""

    ok: bool
    table: str
    executed_sql: str
    row_count: int
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


@mcp.tool(
    title="Run a read-only SQL query",
    annotations=READ_ONLY,
)
async def query_database(sql: str) -> QueryResult:
    """Run one SELECT statement against the database and return the rows.

    Only a single SELECT is accepted. Every statement must carry a LIMIT of at
    most 1000 rows, except an ungrouped aggregate such as `SELECT count(*)`.
    Anything else, including INSERT, UPDATE, DELETE, DDL, and a mutation hidden
    in a CTE, is refused before it reaches the database.

    Read `postgres://schema` and `postgres://relationships` first if you do not
    already know the table and column names.

    Args:
        sql: One SELECT statement, for example
            `SELECT id, email FROM users WHERE active LIMIT 20`.
    """
    try:
        plan = validate_read_query(sql, max_rows=MAX_ROWS)
    except SecurityError as exc:
        # Refused. No pool was touched, no connection was acquired.
        log.info("query refused: %s", exc)
        return QueryResult(
            ok=False,
            executed_sql="",
            row_count=0,
            error=f"Refused by the SQL validator: {exc}",
        )

    notice = ""
    if plan.limit_was_clamped:
        notice = f"The LIMIT was reduced to the server maximum of {MAX_ROWS}."

    try:
        async with db.read_connection() as conn:
            records = await conn.fetch(plan.sql)
    except DatabaseNotConfigured as exc:
        return QueryResult(ok=False, executed_sql=plan.sql, row_count=0, error=str(exc))
    except asyncpg.PostgresError as exc:
        # The database's own refusals land here, including the ones that matter
        # most: `permission denied`, and `canceling statement due to statement
        # timeout`. Those are the read-only role doing the job the validator
        # only approximates.
        return QueryResult(
            ok=False,
            executed_sql=plan.sql,
            row_count=0,
            error=f"PostgreSQL refused the query: {exc}",
        )

    columns = list(records[0].keys()) if records else []
    return QueryResult(
        ok=True,
        executed_sql=plan.sql,
        row_count=len(records),
        columns=columns,
        rows=rows_to_dicts(records),
        notice=notice,
    )


@mcp.tool(
    title="Sample rows from a table",
    annotations=READ_ONLY,
)
async def sample_table(table: str, limit: int = 10) -> TableSample:
    """Read the first few rows of one table, to see the shape of the data.

    Faster and safer than writing a query when all you want is a look at what
    a column actually contains. The table name is checked against the
    database's own catalog before it is used.

    Args:
        table: A table name in the public schema, for example `orders`.
        limit: How many rows to return, from 1 to 100. Defaults to 10.
    """
    try:
        sql, records = await fetch_table_sample(table, limit)
    except LookupError as exc:
        return TableSample(
            ok=False, table=table, executed_sql="", row_count=0, error=str(exc)
        )
    except DatabaseNotConfigured as exc:
        return TableSample(
            ok=False, table=table, executed_sql="", row_count=0, error=str(exc)
        )
    except asyncpg.PostgresError as exc:
        return TableSample(
            ok=False,
            table=table,
            executed_sql="",
            row_count=0,
            error=f"PostgreSQL refused the query: {exc}",
        )

    columns = list(records[0].keys()) if records else []
    return TableSample(
        ok=True,
        table=table,
        executed_sql=sql,
        row_count=len(records),
        columns=columns,
        rows=rows_to_dicts(records),
    )


# Re-exported so callers do not have to reach into schema.py for the ceiling
# the docstring above quotes.
__all__ = ["MAX_SAMPLE_ROWS", "QueryResult", "TableSample", "query_database", "sample_table"]
