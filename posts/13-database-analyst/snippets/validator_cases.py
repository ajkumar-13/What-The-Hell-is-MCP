"""Post 13, sections 6 and 7: what the SQL validator allows, refuses, rewrites, and cannot see.

Run from the series root:
    uv run python posts/13-database-analyst/snippets/validator_cases.py

Calls the project's validator, src/pg_analyst/security.py under
code/13-postgres-analyst/, directly: no server, no client, no database. It
prints the sqlglot release and the sizes of the two lists the post quotes, the
statements of section 6 with the validator's verdict on each, and then, for
section 7, what sqlglot hands back for syntax it cannot model: the warning it
logs, the node types in the tree, what the tree-reading checks find in it, and
the validator's refusal.

The validator needs sqlglot, which the series root's environment pins too
(30.13.0, as the project's lock does); should it lack the project's
dependencies, the script runs itself again under
`uv run --project code/13-postgres-analyst`.
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
    import sqlglot
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

import logging  # noqa: E402
import textwrap  # noqa: E402

# Keep a .env on this machine from pointing the project at a real database.
os.environ.update(DATABASE_URL="", DATABASE_WRITE_URL="", ENABLE_WRITES="false")
sys.path.insert(0, str(PROJECT / "src"))

from pg_analyst import security  # noqa: E402
from pg_analyst.security import SecurityError, validate_read_query  # noqa: E402


class Collect(logging.Handler):
    """Keep what sqlglot logs, so it can be printed beside the tree it describes."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def wrap(text: str, indent: str = "  ", first: str | None = None) -> str:
    return textwrap.fill(text, width=88, initial_indent=first or indent, subsequent_indent=indent)


def verdict(sql: str) -> None:
    print(repr(sql))
    try:
        plan = validate_read_query(sql)
    except SecurityError as exc:
        print(wrap(str(exc), indent="     ", first="  -> "))
        return
    print(f"  -> allowed, runs as {plan.sql!r}  limit={plan.limit} clamped={plan.limit_was_clamped}")


def walk_types(tree) -> list[str]:
    return sorted({type(node).__name__ for node in tree.walk()})


def main() -> None:
    print(f"sqlglot {sqlglot.__version__}; {len(security.DANGEROUS_FUNCTIONS)} blocked functions; "
          f"{len(security._MUTATIONS)} mutation node types")

    print("\n== section 6: a string literal is not a call ==")
    literal = "SELECT * FROM users WHERE full_name = 'pg_sleep' LIMIT 1"
    plan = validate_read_query(literal)
    print(f"in : {literal!r}")
    print(f"out: {plan.sql!r}")
    print(f"regenerated statement identical to the input: {plan.sql == literal}")

    print("\n== section 6: a mutation inside a common table expression ==")
    cte = "WITH doomed AS (DELETE FROM users RETURNING *) SELECT * FROM doomed LIMIT 1"
    tree = sqlglot.parse_one(cte, dialect="postgres")
    print(f"root type  = {type(tree).__name__}")
    print(f"walk types = {walk_types(tree)}")
    verdict(cte)

    print("\n== section 6: refusals ==")
    for sql in (
        "SELECT 1 LIMIT 1; DROP TABLE users;",
        "SELECT * FROM users",
        "SELECT pg_sleep(30) LIMIT 1",
        "SELECT * INTO evil FROM users LIMIT 1",
    ):
        verdict(sql)

    print("\n== section 6: LIMIT, clamped, exempt, and not exempt ==")
    for sql in (
        "SELECT * FROM users LIMIT 100000",
        "SELECT count(*) FROM users",
        "SELECT country, count(*) FROM users GROUP BY country",
    ):
        verdict(sql)

    print("\n== section 6: what runs is regenerated from the tree ==")
    verdict("SELECT   1 /* ; DROP TABLE users */ LIMIT 1  ")

    print("\n== section 7: syntax sqlglot cannot model ==")
    collect = Collect()
    logging.getLogger("sqlglot").addHandler(collect)
    for sql in (
        "DO $$ BEGIN PERFORM pg_sleep(30); END $$",
        "EXPLAIN ANALYZE DELETE FROM users",
        "VACUUM FULL users",
        "CALL do_it()",
    ):
        collect.messages.clear()
        tree = sqlglot.parse_one(sql, dialect="postgres")
        print(repr(sql))
        for message in collect.messages:
            print(f"    sqlglot logged    : {message}")
        print(f"    type(tree)        = {type(tree).__name__}")
        print(f"    walk types        = {walk_types(tree)}")
        print(f"    function_names()  = {sorted(security.function_names(tree))}")
        security.check_dangerous_functions(tree)
        security.reject_nested_mutations(tree)
        print("    the blocklist and nested-mutation checks raise nothing")
        print(f"    a read root?      = {isinstance(tree, security._READ_ROOTS)}")
        try:
            security.enforce_limit(tree)
            print("    enforce_limit()   : passes")
        except SecurityError as exc:
            print(f"    enforce_limit()   : refuses, {exc}")
    logging.getLogger("sqlglot").removeHandler(collect)

    print("\nand the validator, on the same four:")
    refusals = set()
    for sql in ("DO $$ BEGIN PERFORM pg_sleep(30); END $$", "EXPLAIN ANALYZE DELETE FROM users",
                "VACUUM FULL users", "CALL do_it()"):
        try:
            validate_read_query(sql)
        except SecurityError as exc:
            refusals.add(str(exc))
    print(f"  {len(refusals)} distinct refusal for all four:")
    for message in sorted(refusals):
        print(wrap(message, indent="  "))


if __name__ == "__main__":
    # sqlglot's warnings also reach the project's stderr handler; keep stdout to the transcript.
    main()
