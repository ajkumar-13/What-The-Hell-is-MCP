"""Schema introspection, exposed as resources.

Post 13. The model cannot write a correct query against a database it has never
seen, and pasting a schema into the system prompt goes stale the moment someone
runs a migration. Introspection at read time is the fix, and a resource rather
than a tool is the right primitive: this is context a user attaches, not an
action a model decides to take.

Everything here reads through the read-only pool.
"""

from __future__ import annotations

import logging

import asyncpg
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from sqlglot import exp

from .app import mcp
from .database import DatabaseNotConfigured, db

log = logging.getLogger("postgres-analyst.schema")

#: The one schema this server will look at. Widening this is a decision with
#: consequences: `information_schema` and `pg_catalog` describe the database's
#: own plumbing, and a role that can read them can enumerate every other user.
INTROSPECTED_SCHEMA = "public"

#: Ceiling on `sample_table`. Ten rows is enough to see the shape of the data;
#: a thousand is a context-window problem.
MAX_SAMPLE_ROWS = 100


# Constraints are read from `pg_catalog`, not from `information_schema`, for
# two reasons, and an earlier version of this file had both bugs.
#
# 1. The views filter by privilege. `information_schema.table_constraints`
#    lists only constraints on tables the current user owns or holds a
#    privilege other than SELECT on, and `constraint_column_usage` only columns
#    of tables a currently enabled role owns. The read role holds SELECT and
#    nothing else, so through those views it saw no primary key and no foreign
#    key at all.
# 2. The views identify a constraint by schema and name, and PostgreSQL does
#    not make every constraint name unique per schema. A primary key's name is
#    also its index's name, so it is unique per schema; a foreign key's name is
#    unique only per table. Two tables in one schema may each carry a foreign
#    key named `fk_owner`, and a join on the name pairs each one's columns with
#    the other's, in a Markdown document whose entire job is telling a model
#    how the tables join.
#
# `pg_constraint` names the table a constraint belongs to by `conrelid` and the
# table a foreign key points at by `confrelid`, so every join below is on the
# table itself, and the catalog is readable by every role. The columns still
# come from `information_schema.columns`, which shows every column the role can
# read.
_COLUMNS_SQL = """
SELECT
    c.table_name,
    c.column_name,
    c.data_type,
    c.is_nullable,
    c.column_default,
    EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint con
        JOIN pg_catalog.pg_class cls ON cls.oid = con.conrelid
        JOIN pg_catalog.pg_namespace ns ON ns.oid = cls.relnamespace
        JOIN pg_catalog.pg_attribute att
            ON att.attrelid = con.conrelid
           AND att.attnum = ANY (con.conkey)
        WHERE con.contype = 'p'
          AND ns.nspname::text = c.table_schema::text
          AND cls.relname::text = c.table_name::text
          AND att.attname::text = c.column_name::text
    ) AS is_primary_key
FROM information_schema.columns c
WHERE c.table_schema = $1::text
  AND ($2::text IS NULL OR c.table_name::text = $2::text)
ORDER BY c.table_name, c.ordinal_position
"""

# One row per column pair of each foreign key. `unnest` with two arrays walks
# `conkey` and `confkey` side by side, so a composite key's columns stay paired
# in their declared order.
_FOREIGN_KEYS_SQL = """
SELECT
    src.relname::text      AS from_table,
    src_col.attname::text  AS from_column,
    dst.relname::text      AS to_table,
    dst_col.attname::text  AS to_column,
    con.conname::text      AS constraint_name
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class src ON src.oid = con.conrelid
JOIN pg_catalog.pg_namespace ns ON ns.oid = src.relnamespace
JOIN pg_catalog.pg_class dst ON dst.oid = con.confrelid
CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
    WITH ORDINALITY AS k(from_attnum, to_attnum, ord)
JOIN pg_catalog.pg_attribute src_col
    ON src_col.attrelid = con.conrelid
   AND src_col.attnum = k.from_attnum
JOIN pg_catalog.pg_attribute dst_col
    ON dst_col.attrelid = con.confrelid
   AND dst_col.attnum = k.to_attnum
WHERE con.contype = 'f'
  AND ns.nspname::text = $1::text
ORDER BY src.relname, con.conname, k.ord
"""

# The `::text` casts are not decoration. `information_schema` columns are the
# `sql_identifier` domain, and comparing one to a bare placeholder leaves
# PostgreSQL inferring a domain type for the parameter. Casting the parameter
# side pins it to `text` and takes the ambiguity out.
_TABLE_EXISTS_SQL = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = $1::text
  AND table_name = $2::text
  AND table_type = 'BASE TABLE'
"""


def _escape(text: object) -> str:
    """Escape pipes so database content cannot forge extra Markdown rows.

    Table and column names are data, and data from a database an untrusted
    party can write to is untrusted input. This is the same defense the
    resources in post 07 used against process names.
    """
    return str(text).replace("|", "\\|")


async def fetch_columns(table: str | None = None) -> list[asyncpg.Record]:
    async with db.read_connection() as conn:
        return await conn.fetch(_COLUMNS_SQL, INTROSPECTED_SCHEMA, table)


async def fetch_foreign_keys() -> list[asyncpg.Record]:
    async with db.read_connection() as conn:
        return await conn.fetch(_FOREIGN_KEYS_SQL, INTROSPECTED_SCHEMA)


async def resolve_table_name(conn: asyncpg.Connection, table: str) -> str:
    """Confirm a table exists, and return the catalog's spelling of its name.

    PostgreSQL will not accept an identifier as a bind parameter, so the name
    has to be interpolated into the statement text, and that is exactly where
    injection lives. The safe construction is this one: look the caller's
    string up in `information_schema` as a *parameter*, then build the query
    from the name the catalog handed back rather than from the caller's
    string. The catalog is the allowlist, and it cannot contain a name that
    is not really a table.
    """
    row = await conn.fetchrow(_TABLE_EXISTS_SQL, INTROSPECTED_SCHEMA, table)
    if row is None:
        raise LookupError(
            f"There is no table named '{table}' in the {INTROSPECTED_SCHEMA} schema."
        )
    return row["table_name"]


async def fetch_table_sample(table: str, limit: int) -> tuple[str, list[asyncpg.Record]]:
    """A few rows from one table, with the identifier taken from the catalog.

    An earlier version of this built `f"SELECT * FROM {table} LIMIT {limit}"`
    straight from the tool arguments, which is a SQL injection with a friendly
    docstring on top. Two changes fix it: the table name comes back from
    `resolve_table_name` above, and the limit is coerced to an `int` and
    clamped, so neither value can carry syntax.
    """
    limit = max(1, min(int(limit), MAX_SAMPLE_ROWS))

    async with db.read_connection() as conn:
        name = await resolve_table_name(conn, table)

        # Built through sqlglot rather than by string formatting, so the
        # identifier is quoted by a component whose job is quoting identifiers.
        statement = (
            exp.select(exp.Star())
            .from_(
                exp.table_(
                    exp.to_identifier(name, quoted=True),
                    db=exp.to_identifier(INTROSPECTED_SCHEMA, quoted=True),
                )
            )
            .limit(limit)
        )
        sql = statement.sql(dialect="postgres")
        return sql, await conn.fetch(sql)


def render_schema_markdown(rows: list[asyncpg.Record]) -> str:
    """Markdown, because models read Markdown tables well and JSON poorly."""
    if not rows:
        return f"No tables found in the `{INTROSPECTED_SCHEMA}` schema."

    lines = [
        "# Database schema",
        "",
        f"Every base table in the `{INTROSPECTED_SCHEMA}` schema that the "
        "read-only role is allowed to see.",
        "",
    ]

    current: str | None = None
    tables = 0

    for row in rows:
        table = row["table_name"]
        if table != current:
            if current is not None:
                lines.append("")
            tables += 1
            current = table
            lines.append(f"## `{_escape(table)}`")
            lines.append("")
            lines.append("| Column | Type | Nullable | Key |")
            lines.append("| --- | --- | --- | --- |")

        nullable = "yes" if row["is_nullable"] == "YES" else "no"
        key = "PK" if row["is_primary_key"] else ""
        lines.append(
            f"| `{_escape(row['column_name'])}` | {_escape(row['data_type'])} "
            f"| {nullable} | {key} |"
        )

    lines.append("")
    lines.append(f"{tables} table(s).")
    return "\n".join(lines)


def render_foreign_keys_markdown(rows: list[asyncpg.Record]) -> str:
    if not rows:
        return "No foreign key constraints found."

    lines = [
        "# Table relationships",
        "",
        "Use these to join correctly rather than guessing at column names.",
        "",
        "| From | Column | To | Column |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{_escape(row['from_table'])}` | `{_escape(row['from_column'])}` "
            f"| `{_escape(row['to_table'])}` | `{_escape(row['to_column'])}` |"
        )
    return "\n".join(lines)


@mcp.resource(
    "postgres://schema",
    title="Database schema",
    description="Every table and column the read-only role can see.",
    mime_type="text/markdown",
)
async def schema_resource() -> str:
    """The whole schema, as Markdown."""
    try:
        rows = await fetch_columns()
    except DatabaseNotConfigured as exc:
        return f"Schema unavailable: {exc}"
    return render_schema_markdown(rows)


@mcp.resource(
    "postgres://schema/{table}",
    title="One table's columns",
    mime_type="text/markdown",
)
async def table_schema_resource(table: str) -> str:
    """One table's columns, for a database too large to dump whole.

    A template variable is untrusted input. It reaches the query as a bind
    parameter and nothing else, so the worst a hostile value can do is match no
    rows.
    """
    try:
        rows = await fetch_columns(table)
    except DatabaseNotConfigured as exc:
        return f"Schema unavailable: {exc}"

    if not rows:
        # The SDK turns this into -32602 Invalid params for the client.
        raise ResourceNotFoundError(
            f"There is no table named '{table}' in the {INTROSPECTED_SCHEMA} schema."
        )
    return render_schema_markdown(rows)


@mcp.resource(
    "postgres://relationships",
    title="Foreign key relationships",
    description="How the tables join to each other.",
    mime_type="text/markdown",
)
async def relationships_resource() -> str:
    """Foreign keys, as Markdown."""
    try:
        rows = await fetch_foreign_keys()
    except DatabaseNotConfigured as exc:
        return f"Relationships unavailable: {exc}"
    return render_foreign_keys_markdown(rows)
