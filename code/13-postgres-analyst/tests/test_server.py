"""In-memory protocol tests.

Post 12 explains the pattern. `Client(mcp)` connects straight to the server
object with no subprocess and no socket, so these run in milliseconds and are
still going through the real protocol machinery: schemas, validation, result
shapes, and the elicitation round trip.

Note the shape of every test: the client is opened with `async with` inside the
test body, not handed over by a yield fixture. The client owns an anyio task
group, and a task group has to be exited by the same task that entered it. A
yield fixture tears down in a different task and you get "Attempted to exit
cancel scope in a different task" on every test.

Most of these need no database at all, because the validator refuses bad
statements before a pool is ever acquired. The handful that do need one are
marked `needs_database` and skip when `DATABASE_URL` is unset.
"""

from __future__ import annotations

import os

import pytest
from mcp import Client
from mcp_types import ElicitResult

from pg_analyst import mcp
from pg_analyst.security import MAX_ROWS

needs_database = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL is not set; start sql/docker-compose.yml to run these",
)


def answering(action: str, content: dict | None = None, seen: list | None = None):
    """A client whose user always responds to elicitation the same way."""

    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        return ElicitResult(action=action, content=content)

    return Client(mcp, elicitation_callback=callback)


@pytest.fixture(autouse=True)
def _writes_off(monkeypatch):
    """Default every test to a read-only deployment.

    `writes_enabled()` reads the environment on every call, so a test that
    wants the write path turns it on for itself and nothing leaks between
    tests. It also means a stray .env on the machine running the suite cannot
    change what these tests assert.
    """
    monkeypatch.delenv("ENABLE_WRITES", raising=False)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

async def test_the_expected_surface_is_published():
    async with Client(mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        assert tools == {"query_database", "sample_table", "write_database"}

        resources = {str(r.uri) for r in (await c.list_resources()).resources}
        assert resources == {
            "postgres://schema",
            "postgres://relationships",
            "postgres://audit",
        }

        templates = {
            str(t.uri_template) for t in (await c.list_resource_templates()).resource_templates
        }
        assert templates == {"postgres://schema/{table}"}


async def test_every_tool_publishes_an_output_schema():
    """A missing output schema is a silent failure, so assert on it."""
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
        assert tools, "server registered no tools"
        for tool in tools:
            assert tool.output_schema is not None, f"{tool.name} has no output schema"


async def test_every_tool_declares_annotations():
    async with Client(mcp) as c:
        for tool in (await c.list_tools()).tools:
            assert tool.annotations is not None, f"{tool.name} has no annotations"
            assert tool.annotations.read_only_hint is not None


async def test_read_tools_are_marked_read_only_and_the_write_tool_is_not():
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}

        for name in ("query_database", "sample_table"):
            assert tools[name].annotations.read_only_hint is True
            assert tools[name].annotations.destructive_hint is False

        write = tools["write_database"].annotations
        assert write.read_only_hint is False
        assert write.destructive_hint is True
        assert write.idempotent_hint is False


async def test_the_approval_parameter_is_hidden_from_the_model():
    """The security test for the write path.

    `write_database` takes an `approval` parameter resolved by elicitation, and
    it must never appear in the published input schema. If it did, the model
    could pass its own approval and the human gate would be decorative.
    """
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        props = tools["write_database"].input_schema["properties"]
        assert set(props) == {"sql", "dry_run"}


# ---------------------------------------------------------------------------
# The read path, refusals. No database needed: validation happens first.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users WHERE id = 1",
        "DROP TABLE users",
        "SELECT 1 LIMIT 1; DROP TABLE users;",
        "WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d LIMIT 1",
        "SELECT pg_sleep(30) LIMIT 1",
        "SELECT * FROM users",
        "SELECT * INTO evil FROM users LIMIT 1",
    ],
)
async def test_query_database_refuses_before_touching_a_pool(sql):
    async with Client(mcp) as c:
        result = await c.call_tool("query_database", {"sql": sql})
        data = result.structured_content
        assert data["ok"] is False
        assert "Refused by the SQL validator" in data["error"]
        assert data["executed_sql"] == ""
        assert data["rows"] == []


async def test_a_refusal_is_a_result_not_a_protocol_error():
    """A model that gets structured `ok: false` can correct itself next turn.
    A model that gets a JSON-RPC error usually just apologises."""
    async with Client(mcp) as c:
        result = await c.call_tool("query_database", {"sql": "DROP TABLE users"})
        assert result.is_error is not True
        assert result.result_type == "complete"


# ---------------------------------------------------------------------------
# The write path. Every one of these runs with no database.
# ---------------------------------------------------------------------------

async def test_writes_are_disabled_by_default():
    async with answering("accept", {"confirm": True}) as c:
        result = await c.call_tool(
            "write_database", {"sql": "UPDATE users SET active = false WHERE id = 1"}
        )
        assert result.structured_content["status"] == "disabled"


async def test_nobody_is_asked_to_confirm_when_writes_are_disabled():
    """The switch is the outermost gate. If it is off, the user is not even
    interrupted."""
    seen: list[str] = []
    async with answering("accept", {"confirm": True}, seen=seen) as c:
        await c.call_tool(
            "write_database", {"sql": "UPDATE users SET active = false WHERE id = 1"}
        )
    assert seen == []


async def test_an_invalid_write_is_rejected_without_asking_anyone(monkeypatch):
    """Asking a human to approve something the server has already decided to
    refuse teaches them to click through prompts without reading them."""
    monkeypatch.setenv("ENABLE_WRITES", "true")

    seen: list[str] = []
    async with answering("accept", {"confirm": True}, seen=seen) as c:
        result = await c.call_tool("write_database", {"sql": "DELETE FROM users"})
        data = result.structured_content

    assert data["status"] == "rejected"
    assert "WHERE" in data["detail"]
    assert seen == []


async def test_a_dry_run_executes_nothing_and_asks_nothing(monkeypatch):
    monkeypatch.setenv("ENABLE_WRITES", "true")

    seen: list[str] = []
    async with answering("accept", {"confirm": True}, seen=seen) as c:
        result = await c.call_tool(
            "write_database",
            {"sql": "update users set active=false where id = 3", "dry_run": True},
        )
        data = result.structured_content

    assert data["status"] == "dry_run"
    assert data["operation"] == "UPDATE"
    assert data["table"] == "users"
    assert data["executed_sql"] == "UPDATE users SET active = FALSE WHERE id = 3"
    assert data["affected_rows"] == 0
    assert seen == []


async def test_declining_does_not_write(monkeypatch):
    monkeypatch.setenv("ENABLE_WRITES", "true")
    async with answering("decline") as c:
        result = await c.call_tool(
            "write_database", {"sql": "DELETE FROM orders WHERE id = 1"}
        )
        data = result.structured_content
    assert data["status"] == "declined"
    assert "declined" in data["detail"].lower()


async def test_dismissing_the_prompt_does_not_write(monkeypatch):
    monkeypatch.setenv("ENABLE_WRITES", "true")
    async with answering("cancel") as c:
        result = await c.call_tool(
            "write_database", {"sql": "DELETE FROM orders WHERE id = 1"}
        )
        data = result.structured_content
    assert data["status"] == "declined"
    assert "dismissed" in data["detail"].lower()


async def test_answering_no_does_not_write(monkeypatch):
    monkeypatch.setenv("ENABLE_WRITES", "true")
    async with answering("accept", {"confirm": False}) as c:
        result = await c.call_tool(
            "write_database", {"sql": "DELETE FROM orders WHERE id = 1"}
        )
        data = result.structured_content
    assert data["status"] == "declined"
    assert "answered no" in data["detail"].lower()


async def test_the_question_is_asked_once_and_names_the_statement(monkeypatch):
    """The MRTR convergence test.

    The SDK pins a recorded answer to a SHA-256 digest of the exact rendered
    question. A question built from anything other than the tool's arguments,
    a timestamp or a live row count, re-renders differently on the retry, never
    matches its own answer, and the call loops until the round limit.

    One entry in `seen` is the whole assertion.
    """
    monkeypatch.setenv("ENABLE_WRITES", "true")

    seen: list[str] = []
    async with answering("decline", seen=seen) as c:
        await c.call_tool(
            "write_database", {"sql": "DELETE FROM orders WHERE id = 4242"}
        )

    assert len(seen) == 1
    assert "DELETE" in seen[0]
    assert "orders" in seen[0]
    assert "4242" in seen[0]


async def test_the_confirmation_question_is_identical_across_calls(monkeypatch):
    """Same arguments in, same question out, every time. This is the property
    the digest match depends on."""
    monkeypatch.setenv("ENABLE_WRITES", "true")

    seen: list[str] = []
    for _ in range(3):
        async with answering("decline", seen=seen) as c:
            await c.call_tool(
                "write_database", {"sql": "DELETE FROM orders WHERE id = 7"}
            )

    assert len(seen) == 3
    assert len(set(seen)) == 1


async def test_an_approved_write_with_no_write_pool_fails_cleanly(monkeypatch):
    """ENABLE_WRITES on, DATABASE_WRITE_URL missing. The user said yes and
    there is nowhere to send it, which must be a clear result rather than a
    traceback."""
    monkeypatch.setenv("ENABLE_WRITES", "true")
    monkeypatch.delenv("DATABASE_WRITE_URL", raising=False)

    async with answering("accept", {"confirm": True}) as c:
        result = await c.call_tool(
            "write_database", {"sql": "DELETE FROM orders WHERE id = 1"}
        )
        data = result.structured_content

    assert data["status"] == "failed"
    assert "DATABASE_WRITE_URL" in data["detail"]
    assert data["affected_rows"] == 0


# ---------------------------------------------------------------------------
# Resources without a database
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    bool(os.getenv("DATABASE_URL")), reason="asserts the unconfigured message"
)
async def test_resources_explain_themselves_when_there_is_no_pool():
    async with Client(mcp) as c:
        text = (await c.read_resource("postgres://schema")).contents[0].text
        assert "DATABASE_URL is not set" in text


# ---------------------------------------------------------------------------
# Everything below here needs a live PostgreSQL
# ---------------------------------------------------------------------------

@needs_database
async def test_the_schema_resource_lists_the_seeded_tables():
    async with Client(mcp) as c:
        text = (await c.read_resource("postgres://schema")).contents[0].text
        assert "# Database schema" in text
        for table in ("users", "orders", "products"):
            assert f"## `{table}`" in text


@needs_database
async def test_the_schema_resource_marks_primary_keys():
    async with Client(mcp) as c:
        text = (await c.read_resource("postgres://schema/users")).contents[0].text
        pk_lines = [line for line in text.splitlines() if line.endswith("| PK |")]
        assert len(pk_lines) == 1
        assert "`id`" in pk_lines[0]


@needs_database
async def test_an_unknown_table_template_is_rejected():
    async with Client(mcp) as c:
        with pytest.raises(Exception) as excinfo:
            await c.read_resource("postgres://schema/no_such_table")
        assert "no table named" in str(excinfo.value)


@needs_database
async def test_the_relationships_resource_finds_the_foreign_keys():
    async with Client(mcp) as c:
        text = (await c.read_resource("postgres://relationships")).contents[0].text
        assert "`orders`" in text
        assert "`users`" in text


@needs_database
async def test_a_valid_query_returns_structured_rows():
    async with Client(mcp) as c:
        result = await c.call_tool(
            "query_database", {"sql": "SELECT email, country FROM users LIMIT 3"}
        )
        data = result.structured_content
        assert data["ok"] is True
        assert data["row_count"] == 3
        assert data["columns"] == ["email", "country"]
        assert all(set(row) == {"email", "country"} for row in data["rows"])


@needs_database
async def test_an_oversized_limit_is_clamped_and_the_caller_is_told():
    async with Client(mcp) as c:
        result = await c.call_tool(
            "query_database", {"sql": "SELECT id FROM users LIMIT 999999"}
        )
        data = result.structured_content
        assert data["ok"] is True
        assert f"LIMIT {MAX_ROWS}" in data["executed_sql"]
        assert str(MAX_ROWS) in data["notice"]


@needs_database
async def test_non_json_column_types_survive_the_trip():
    """NUMERIC becomes a string rather than a float, because a float would
    quietly lose precision on a money column."""
    async with Client(mcp) as c:
        result = await c.call_tool(
            "query_database", {"sql": "SELECT price, placed_at FROM orders LIMIT 1"}
        )
        row = result.structured_content["rows"][0]
        assert isinstance(row["price"], str)
        assert isinstance(row["placed_at"], str)


@needs_database
async def test_sample_table_uses_the_catalogue_as_its_allowlist():
    async with Client(mcp) as c:
        result = await c.call_tool("sample_table", {"table": "products", "limit": 4})
        data = result.structured_content
        assert data["ok"] is True
        assert data["row_count"] == 4
        assert data["executed_sql"] == 'SELECT * FROM "public"."products" LIMIT 4'


@needs_database
@pytest.mark.parametrize(
    "table",
    [
        "no_such_table",
        'users"; DROP TABLE users; --',
        "users WHERE 1=1",
    ],
)
async def test_sample_table_rejects_anything_not_in_the_catalogue(table):
    """The injection test. None of these name a real table, so the catalogue
    lookup finds nothing and no SQL is ever built from them."""
    async with Client(mcp) as c:
        result = await c.call_tool("sample_table", {"table": table, "limit": 1})
        data = result.structured_content
        assert data["ok"] is False
        assert "no table named" in data["error"]
        assert data["executed_sql"] == ""


@needs_database
async def test_the_read_role_refuses_a_write_that_somehow_got_through():
    """Reach past the validator and call the pool directly, the way a bug in
    the query path would. This is the layer that has to hold when the parser
    does not, and it is the whole argument of post 13."""
    import asyncpg

    from pg_analyst.database import db

    async with Client(mcp):
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            async with db.read_connection() as conn:
                await conn.execute("DELETE FROM orders WHERE id = 1")

    message = str(excinfo.value).lower()
    assert "read-only" in message or "permission denied" in message
