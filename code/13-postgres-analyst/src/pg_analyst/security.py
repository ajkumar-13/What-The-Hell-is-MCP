"""The SQL validation layer, and an honest account of what it cannot do.

Posts 13 and 14. This module has no database dependency at all, which is why
`tests/test_security.py` runs with no PostgreSQL anywhere.

## How it works

Every statement is parsed with `sqlglot` using `dialect="postgres"`, and the
checks below walk the resulting tree. That is a real difference from a
tokenizer or a regex, and it shows up immediately:

    SELECT * FROM users WHERE note = 'pg_sleep' LIMIT 1

A substring blocklist rejects that. The AST does not, because `pg_sleep` there
is a string literal, not a call. Conversely:

    WITH doomed AS (DELETE FROM users RETURNING *) SELECT * FROM doomed LIMIT 1

is a `Select` at the root and passes any "does it start with SELECT" test. The
AST walk finds the `Delete` node nested inside it and refuses.

The statement that eventually runs is regenerated from the tree rather than
taken from the string that arrived, with comments dropped on the way out. Two
things follow from that. Anything the parser did not understand cannot survive
the round trip. And the statement in the confirmation prompt, the statement in
the audit row, and the statement PostgreSQL executes are one string, so there is
no gap between what a human approved and what ran.

## What this layer still cannot catch

Read this part before you decide the parser is the security control. It is not.

- **It has no idea what is sensitive.** `SELECT * FROM users LIMIT 1000` is a
  perfectly valid, perfectly allowed query that returns a thousand rows of
  personal data. Column- and row-level policy is a database concern.
- **A blocklist is a blocklist.** `DANGEROUS_FUNCTIONS` names the functions
  that were dangerous when this was written. PostgreSQL ships new ones, and
  extensions ship more; the list does not update itself.
- **Names lie.** A table can be a view, and a view can sit on top of a
  `SECURITY DEFINER` function that does whatever it likes with the privileges
  of its owner. The parser sees an identifier.
- **Regenerating the SQL trades one bug class for another.** Text the parser
  ignored can no longer ride along; a `sqlglot` generation bug that changes the
  meaning of a statement becomes possible. That trade is worth making, but it
  is a trade, and it is why the tools hand `executed_sql` back to the caller.
- **Valid PostgreSQL gets refused.** Anything `sqlglot` cannot parse comes back
  as an opaque `exp.Command` node, and this module refuses those on sight,
  because a node it cannot see inside is a node it cannot check.
- **Cost is invisible.** A three-way self join with no useful index is a denial
  of service and looks exactly like a normal query in the tree.

The control that actually holds is the one in `sql/002-readonly-role.sql`: a
login role that has been granted `SELECT` and nothing else, with
`default_transaction_read_only`, a `statement_timeout`, and an
`idle_in_transaction_session_timeout`. When this module is wrong, the database
still says no. That ordering, not the parser, is the reason the server is
defensible.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError


class SecurityError(Exception):
    """Raised when a statement is refused before it reaches the database."""


#: The most rows any single read may return. Also the ceiling this module
#: clamps an over-large LIMIT down to.
MAX_ROWS = 1000

#: Functions refused on both the read and the write path. Every one of these
#: does something a SQL analyst has no business doing: stall a backend, kill
#: another session, or touch the server's file system.
DANGEROUS_FUNCTIONS = frozenset(
    {
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "query_to_xml",
        "pg_reload_conf",
        "set_config",
    }
)

# Root node types a read may have. `Union`, `Intersect`, and `Except` are the
# set operators; all four are `exp.Query` subclasses and all four carry a
# top-level LIMIT.
_READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# Root node types a write may have, mapped to the word used in errors, in the
# confirmation prompt, and in the audit row.
_WRITE_ROOTS: dict[type[exp.Expression], str] = {
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
}

# Any of these appearing anywhere below the root is a refusal. `exp.Command` is
# in the list because it is what sqlglot produces when it gives up on a
# fragment: an unparsed node is an unchecked node.
_MUTATIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Grant,
    exp.Revoke,
    exp.Set,
    exp.Command,
)

_NODE_NAMES: dict[type[exp.Expression], str] = {
    exp.Select: "SELECT",
    exp.Union: "UNION",
    exp.Intersect: "INTERSECT",
    exp.Except: "EXCEPT",
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
    exp.Merge: "MERGE",
    exp.Drop: "DROP",
    exp.Create: "CREATE",
    exp.Alter: "ALTER",
    exp.TruncateTable: "TRUNCATE",
    exp.Copy: "COPY",
    exp.Grant: "GRANT",
    exp.Revoke: "REVOKE",
    exp.Set: "SET",
}


@dataclass(frozen=True)
class QueryPlan:
    """A read that passed every check.

    `sql` is regenerated from the tree, so it is what will actually run, and it
    is what gets shown to the caller. It is not necessarily byte-identical to
    what was submitted.
    """

    operation: str
    sql: str
    limit: int | None
    limit_was_clamped: bool


@dataclass(frozen=True)
class WritePlan:
    """A write that passed every check but has not been confirmed or run."""

    operation: str
    table: str
    sql: str


def render(statement: exp.Expression) -> str:
    """Turn a validated tree back into the SQL that will actually run.

    `comments=False` is the load-bearing argument. Without it sqlglot copies
    comments through verbatim, and a comment is a place to hide text from
    somebody skim-reading a confirmation prompt or an audit row. They are inert
    to PostgreSQL, so dropping them costs nothing.
    """
    return statement.sql(dialect="postgres", comments=False)


def describe(node: exp.Expression) -> str:
    """A human-readable name for a node, for error messages."""
    if isinstance(node, exp.Command):
        return "an unparseable command"
    return _NODE_NAMES.get(type(node), type(node).__name__.upper())


def parse_single_statement(sql: str) -> exp.Expression:
    """Parse exactly one statement, or refuse.

    Refusing more than one statement is what stops `SELECT 1; DROP TABLE users`
    at the door, and it is cheaper and far more reliable than trying to decide
    which of several statements is the "real" one.
    """
    text = (sql or "").strip()
    if not text:
        raise SecurityError("Empty statement.")

    try:
        statements = [s for s in sqlglot.parse(text, dialect="postgres") if s is not None]
    except (ParseError, TokenError) as exc:
        raise SecurityError(f"Could not parse this as PostgreSQL: {exc}") from exc

    if not statements:
        raise SecurityError("Empty statement.")

    if len(statements) > 1:
        raise SecurityError(
            f"Submit one statement at a time. This parsed as {len(statements)} "
            "statements, which is the shape of a chained-statement attack."
        )

    statement = statements[0]

    # sqlglot does not raise on syntax it does not model; it wraps the raw text
    # in a Command node and moves on, with only a logger warning. Every check
    # below walks the tree, so an opaque node is a check that silently did not
    # happen. Refuse it explicitly.
    if isinstance(statement, exp.Command):
        raise SecurityError(
            "The parser could not model this statement and fell back to an "
            "opaque command node, so none of the safety checks could look "
            "inside it. Refused."
        )

    return statement


def function_names(statement: exp.Expression) -> set[str]:
    """Every function called anywhere in the tree, lowercased.

    Two cases, and the order matters. `sqlglot` gives functions it knows a
    typed node (`COUNT` becomes `exp.Count`), and functions it does not know an
    `exp.Anonymous` carrying the raw name. `exp.Anonymous` is a subclass of
    `exp.Func`, so it has to be tested first.
    """
    names: set[str] = set()
    for node in statement.walk():
        if isinstance(node, exp.Anonymous):
            names.add(node.name.lower())
        elif isinstance(node, exp.Func):
            names.add(node.sql_name().lower())
    return names


def check_dangerous_functions(statement: exp.Expression) -> None:
    """Refuse a statement that calls anything on the blocklist.

    Post 13's first draft did this with `func in sql.lower()`, which has both
    failure modes: it rejects `WHERE note = 'pg_sleep'`, and it misses
    `pg_sleep` reached through a wrapper. Walking the tree fixes the first
    outright. The second is unfixable here and is why the read role's
    `statement_timeout` exists.
    """
    hits = sorted(function_names(statement) & DANGEROUS_FUNCTIONS)
    if hits:
        raise SecurityError(
            f"Function '{hits[0]}' is not allowed. It can stall a backend, "
            "reach the server's file system, or interfere with other sessions."
        )


def reject_nested_mutations(root: exp.Expression) -> None:
    """Refuse any mutation below the root node.

    This is the check that stops a data-modifying CTE. PostgreSQL genuinely
    allows `WITH x AS (DELETE ... RETURNING *) SELECT * FROM x`, and it is a
    `Select` at the root, so anything that only looks at the first keyword lets
    it straight through.
    """
    for node in root.walk():
        if node is root:
            continue
        if isinstance(node, _MUTATIONS):
            raise SecurityError(
                f"A nested {describe(node)} appears inside this statement. "
                "A CTE or subquery cannot carry a mutation past this layer."
            )


def is_aggregate_only(statement: exp.Expression) -> bool:
    """True for `SELECT count(*) FROM t` and friends.

    An ungrouped aggregate returns one row by construction, so requiring a
    LIMIT on it would be noise. A grouped aggregate returns one row per group
    and gets no exemption.
    """
    if not isinstance(statement, exp.Select):
        return False
    if statement.args.get("group"):
        return False
    projections = statement.expressions
    if not projections:
        return False
    return all(isinstance(p.unalias(), exp.AggFunc) for p in projections)


def enforce_limit(
    statement: exp.Expression, max_rows: int = MAX_ROWS
) -> tuple[exp.Expression, int | None, bool]:
    """Require a LIMIT, and cap it.

    Returns the (possibly rewritten) statement, the effective limit, and
    whether it had to be clamped.

    A missing LIMIT is refused rather than silently added. The model is capable
    of writing `LIMIT 50`, and a query that quietly returns a different result
    set than the one that was written is worse for everybody than an error
    message that says exactly what to add.
    """
    limit = statement.args.get("limit")

    if limit is None:
        if is_aggregate_only(statement):
            return statement, None, False
        raise SecurityError(
            f"Every query must carry a LIMIT of at most {max_rows} rows. "
            "Add 'LIMIT n' to the statement."
        )

    value = limit.expression
    if not isinstance(value, exp.Literal) or value.is_string:
        raise SecurityError(
            "LIMIT must be a plain integer literal. A parameter or an "
            "expression there cannot be checked before the query runs."
        )

    try:
        requested = int(value.name)
    except ValueError as exc:
        raise SecurityError("LIMIT must be a plain integer literal.") from exc

    if requested < 1:
        raise SecurityError("LIMIT must be at least 1.")

    if requested > max_rows:
        return statement.limit(max_rows), max_rows, True

    return statement, requested, False


def validate_read_query(sql: str, max_rows: int = MAX_ROWS) -> QueryPlan:
    """Validate a statement for the read path. Raises `SecurityError`."""
    statement = parse_single_statement(sql)

    if not isinstance(statement, _READ_ROOTS):
        raise SecurityError(
            f"Only SELECT is allowed here, and this is {describe(statement)}. "
            "Use the write_database tool for changes."
        )

    reject_nested_mutations(statement)

    # SELECT ... INTO creates a table, which is a write wearing a SELECT's
    # clothes. It is a Select at the root and carries no mutation node, so it
    # needs its own check.
    if statement.args.get("into") is not None or statement.find(exp.Into) is not None:
        raise SecurityError(
            "SELECT ... INTO creates a new table. Refused on the read path."
        )

    check_dangerous_functions(statement)
    statement, limit, clamped = enforce_limit(statement, max_rows)

    return QueryPlan(
        operation="SELECT",
        sql=render(statement),
        limit=limit,
        limit_was_clamped=clamped,
    )


def validate_write_statement(sql: str) -> WritePlan:
    """Validate a statement for the write path. Raises `SecurityError`.

    Note what is *not* here: no DDL, no `TRUNCATE`, no `COPY`, no `GRANT`, no
    `MERGE`. A model with a shell into your schema is a different product from
    a model that can correct a row, and this server is the second one.
    """
    statement = parse_single_statement(sql)

    operation = _WRITE_ROOTS.get(type(statement))
    if operation is None:
        raise SecurityError(
            f"Only INSERT, UPDATE, and DELETE are allowed here, and this is "
            f"{describe(statement)}. Schema changes are not this server's job."
        )

    reject_nested_mutations(statement)
    check_dangerous_functions(statement)

    # An UPDATE or DELETE with no WHERE rewrites or removes every row in the
    # table. This is the single most common way an assistant with write access
    # ruins somebody's afternoon.
    #
    # It is a floor, not a guarantee: `WHERE true` satisfies it. What actually
    # bounds the damage is that a human reads the statement in the confirmation
    # prompt before it runs, and that the whole thing is one transaction.
    if operation in ("UPDATE", "DELETE") and statement.args.get("where") is None:
        raise SecurityError(
            f"{operation} without a WHERE clause would touch every row in the "
            "table. Add a WHERE clause naming the rows you mean."
        )

    return WritePlan(
        operation=operation,
        table=target_table(statement),
        sql=render(statement),
    )


def target_table(statement: exp.Expression) -> str:
    """The table a write is aimed at, for the prompt and the audit row."""
    target = statement.this
    if isinstance(target, exp.Schema):
        # INSERT INTO t (a, b) parses the column list as a Schema wrapping the
        # Table, so the table is one level further down.
        target = target.this
    if isinstance(target, exp.Table):
        return target.sql(dialect="postgres")

    found = statement.find(exp.Table)
    return found.sql(dialect="postgres") if found is not None else "unknown"
