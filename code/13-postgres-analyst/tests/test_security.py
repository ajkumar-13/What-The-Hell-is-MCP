"""Unit tests for the SQL validator. No database, no server, no event loop.

Post 12 calls this the first of the three levels of test, and it is the level
that matters most here: the validator is a pure function of a string, so there
is no excuse for not testing every rule in it exhaustively and in milliseconds.

The last section is the interesting one. It tests things the validator
deliberately allows through, because a security layer whose limits are not
written down anywhere is a security layer people will over-trust.
"""

from __future__ import annotations

import pytest

from pg_analyst.security import (
    MAX_ROWS,
    QueryPlan,
    SecurityError,
    WritePlan,
    is_aggregate_only,
    parse_single_statement,
    validate_read_query,
    validate_write_statement,
)


# ---------------------------------------------------------------------------
# Reads that must pass
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users LIMIT 10",
        "select id, email from users where active limit 5",
        "SELECT u.email, o.total FROM users u JOIN orders o ON o.user_id = u.id LIMIT 25",
        "WITH recent AS (SELECT * FROM orders LIMIT 100) SELECT * FROM recent LIMIT 10",
        "SELECT * FROM users UNION SELECT * FROM users LIMIT 5",
        "SELECT count(*) FROM users",
        "SELECT count(*) AS n FROM orders",
        "SELECT sum(total), avg(total) FROM orders",
        "SELECT * FROM users ORDER BY created_at DESC LIMIT 1000",
    ],
)
def test_valid_reads_are_accepted(sql):
    plan = validate_read_query(sql)
    assert isinstance(plan, QueryPlan)
    assert plan.operation == "SELECT"
    assert plan.sql


# ---------------------------------------------------------------------------
# Reads that must be refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("", "Empty"),
        ("   ", "Empty"),
        ("DELETE FROM users WHERE id = 1", "Only SELECT"),
        ("UPDATE users SET active = false WHERE id = 1", "Only SELECT"),
        ("INSERT INTO users (email) VALUES ('x') ", "Only SELECT"),
        ("DROP TABLE users", "Only SELECT"),
        ("TRUNCATE TABLE users", "Only SELECT"),
        ("CREATE TABLE evil (id int)", "Only SELECT"),
        ("ALTER TABLE users ADD COLUMN evil text", "Only SELECT"),
        ("GRANT SELECT ON users TO PUBLIC", "Only SELECT"),
        ("SET statement_timeout = 0", "Only SELECT"),
        ("COPY users TO '/tmp/dump.csv'", "Only SELECT"),
    ],
)
def test_non_select_statements_are_refused(sql, expected):
    with pytest.raises(SecurityError) as excinfo:
        validate_read_query(sql)
    assert expected in str(excinfo.value)


def test_chained_statements_are_refused():
    """The classic. Two statements is one too many, whatever they are."""
    with pytest.raises(SecurityError) as excinfo:
        validate_read_query("SELECT 1 LIMIT 1; DROP TABLE users;")
    assert "one statement at a time" in str(excinfo.value)


def test_a_mutation_hidden_in_a_cte_is_refused():
    """The reason this module uses a parser rather than a tokeniser.

    PostgreSQL really does execute this, and it really is a `Select` at the
    root, so every check that only looks at the leading keyword lets it past.
    """
    sql = "WITH doomed AS (DELETE FROM users RETURNING *) SELECT * FROM doomed LIMIT 1"
    with pytest.raises(SecurityError) as excinfo:
        validate_read_query(sql)
    assert "nested DELETE" in str(excinfo.value)


def test_a_mutation_hidden_in_a_subquery_is_refused():
    sql = "SELECT * FROM (SELECT 1) x LIMIT 1"
    validate_read_query(sql)  # the control: this one is fine

    sql = "WITH w AS (INSERT INTO users (email) VALUES ('a') RETURNING *) SELECT * FROM w LIMIT 1"
    with pytest.raises(SecurityError) as excinfo:
        validate_read_query(sql)
    assert "nested INSERT" in str(excinfo.value)


def test_select_into_is_refused():
    with pytest.raises(SecurityError) as excinfo:
        validate_read_query("SELECT * INTO evil FROM users LIMIT 1")
    assert "INTO" in str(excinfo.value)


def test_unparseable_sql_is_refused():
    with pytest.raises(SecurityError) as excinfo:
        validate_read_query("this is not sql @@@")
    assert "parse" in str(excinfo.value).lower()


def test_syntax_sqlglot_cannot_model_is_refused_not_waved_through():
    """`sqlglot` does not raise on syntax it does not know: it wraps the text
    in an opaque `Command` node and carries on with only a log warning. Every
    check in the module walks the tree, so an opaque node means the checks
    silently did not run. Refusing is the only safe reading."""
    with pytest.raises(SecurityError) as excinfo:
        parse_single_statement("VACUUM FULL users")
    assert "opaque command" in str(excinfo.value)

    with pytest.raises(SecurityError):
        parse_single_statement("DO $$ BEGIN PERFORM pg_sleep(30); END $$")


# ---------------------------------------------------------------------------
# Dangerous functions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(30) LIMIT 1",
        "SELECT * FROM pg_read_file('/etc/passwd') LIMIT 1",
        "SELECT lo_import('/etc/shadow') LIMIT 1",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity LIMIT 1",
        "SELECT id FROM users WHERE id = (SELECT pg_sleep(5)) LIMIT 1",
    ],
)
def test_dangerous_functions_are_refused(sql):
    with pytest.raises(SecurityError) as excinfo:
        validate_read_query(sql)
    assert "not allowed" in str(excinfo.value)


def test_a_function_name_inside_a_string_literal_is_not_a_function_call():
    """The payoff for walking the AST instead of searching the text.

    Post 13's first draft did `if func in sql.lower()`, which rejects this
    entirely legitimate query. The tree knows the difference between a call and
    a string.
    """
    plan = validate_read_query(
        "SELECT * FROM users WHERE full_name = 'pg_sleep' LIMIT 1"
    )
    assert "pg_sleep" in plan.sql


def test_a_column_named_after_a_blocked_function_is_still_fine():
    validate_read_query("SELECT pg_read_file FROM audit_notes LIMIT 1")


# ---------------------------------------------------------------------------
# LIMIT enforcement
# ---------------------------------------------------------------------------

def test_a_query_without_a_limit_is_refused():
    with pytest.raises(SecurityError) as excinfo:
        validate_read_query("SELECT * FROM users")
    assert "LIMIT" in str(excinfo.value)


def test_an_ungrouped_aggregate_does_not_need_a_limit():
    plan = validate_read_query("SELECT count(*) FROM users")
    assert plan.limit is None
    assert not plan.limit_was_clamped


def test_a_grouped_aggregate_does_need_a_limit():
    """One row per group is not one row, so the exemption does not apply."""
    assert not is_aggregate_only(
        parse_single_statement("SELECT country, count(*) FROM users GROUP BY country")
    )
    with pytest.raises(SecurityError):
        validate_read_query("SELECT country, count(*) FROM users GROUP BY country")


def test_an_oversized_limit_is_clamped_rather_than_refused():
    plan = validate_read_query("SELECT * FROM users LIMIT 100000")
    assert plan.limit == MAX_ROWS
    assert plan.limit_was_clamped
    assert f"LIMIT {MAX_ROWS}" in plan.sql


def test_a_limit_within_the_ceiling_is_left_alone():
    plan = validate_read_query("SELECT * FROM users LIMIT 7")
    assert plan.limit == 7
    assert not plan.limit_was_clamped


@pytest.mark.parametrize("sql", ["SELECT * FROM users LIMIT $1", "SELECT * FROM users LIMIT 0"])
def test_a_limit_that_cannot_be_checked_up_front_is_refused(sql):
    with pytest.raises(SecurityError) as excinfo:
        validate_read_query(sql)
    assert "LIMIT" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Regeneration
# ---------------------------------------------------------------------------

def test_the_statement_that_runs_is_regenerated_from_the_tree():
    """What executes is what was parsed, not what arrived.

    Anything the parser did not understand cannot survive the round trip, which
    is what makes comment smuggling and stray trailing whitespace a non-issue.
    """
    plan = validate_read_query("SELECT   1 /* ; DROP TABLE users */ LIMIT 1  ")
    assert "DROP" not in plan.sql
    assert plan.sql == "SELECT 1 LIMIT 1"


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("sql", "operation", "table"),
    [
        ("INSERT INTO users (email, full_name, country) VALUES ('a@b.c', 'A', 'GB')", "INSERT", "users"),
        ("UPDATE users SET active = false WHERE id = 3", "UPDATE", "users"),
        ("DELETE FROM orders WHERE status = 'cancelled'", "DELETE", "orders"),
    ],
)
def test_valid_writes_are_accepted(sql, operation, table):
    plan = validate_write_statement(sql)
    assert isinstance(plan, WritePlan)
    assert plan.operation == operation
    assert plan.table == table


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE users SET active = false",
        "DELETE FROM users",
    ],
)
def test_update_and_delete_without_a_where_are_refused(sql):
    with pytest.raises(SecurityError) as excinfo:
        validate_write_statement(sql)
    assert "WHERE" in str(excinfo.value)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users LIMIT 1",
        "DROP TABLE users",
        "TRUNCATE TABLE users",
        "ALTER TABLE users ADD COLUMN evil text",
        "CREATE TABLE evil (id int)",
        "GRANT ALL ON users TO PUBLIC",
        "MERGE INTO users u USING staging s ON u.id = s.id WHEN MATCHED THEN UPDATE SET u.email = s.email",
    ],
)
def test_writes_outside_insert_update_delete_are_refused(sql):
    with pytest.raises(SecurityError) as excinfo:
        validate_write_statement(sql)
    assert "Only INSERT, UPDATE, and DELETE" in str(excinfo.value)


def test_a_second_statement_chained_onto_a_write_is_refused():
    with pytest.raises(SecurityError) as excinfo:
        validate_write_statement(
            "UPDATE users SET active = false WHERE id = 1; DROP TABLE users;"
        )
    assert "one statement at a time" in str(excinfo.value)


def test_a_write_with_a_dangerous_function_is_refused():
    with pytest.raises(SecurityError):
        validate_write_statement(
            "UPDATE users SET full_name = pg_read_file('/etc/passwd') WHERE id = 1"
        )


def test_the_write_plan_carries_the_regenerated_statement():
    """The prompt the human reads and the statement that runs are the same
    string, because both come from `plan.sql`."""
    plan = validate_write_statement(
        "update  users   set active = false where id = 3 /* and everything else */"
    )
    assert plan.sql == "UPDATE users SET active = FALSE WHERE id = 3"


# ---------------------------------------------------------------------------
# What this layer does NOT stop
#
# These are not bugs. They are the boundary of what a parser can know, and the
# reason sql/002-readonly-role.sql exists.
# ---------------------------------------------------------------------------

def test_a_full_table_read_of_sensitive_data_is_perfectly_valid_sql():
    """The validator has no concept of "sensitive". Column-level policy is a
    database concern, not a parser concern."""
    plan = validate_read_query("SELECT email, full_name FROM users LIMIT 1000")
    assert plan.limit == 1000


def test_a_where_clause_that_matches_everything_satisfies_the_where_rule():
    """`WHERE true` is a WHERE clause. The rule is a floor against the
    fat-fingered `UPDATE users SET ...` with no predicate at all; what actually
    bounds this is the human reading the statement in the confirmation prompt.
    """
    plan = validate_write_statement("UPDATE users SET active = false WHERE true")
    assert plan.operation == "UPDATE"


def test_an_expensive_query_looks_exactly_like_a_cheap_one():
    """Cost is invisible to a parser. The role's statement_timeout is what
    answers this, and it answers it server-side, 30 seconds in."""
    validate_read_query(
        "SELECT * FROM orders a, orders b, orders c WHERE a.id <> b.id LIMIT 1000"
    )


def test_the_validator_cannot_tell_a_table_from_a_view():
    """`users` here could be a view over a SECURITY DEFINER function that does
    something entirely different. The parser sees an identifier."""
    validate_read_query("SELECT * FROM users LIMIT 1")
