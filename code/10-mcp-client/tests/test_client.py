"""The client and the host, end to end. Posts 10 and 11.

Every test here drives a real client against the real server from
`code/05-first-server`, in memory: no subprocess, no socket, and no network.
`Client(server_object)` connects straight through, so the whole file runs in
seconds while still going over the genuine protocol path -- schemas,
validation, result shapes, and the multi-round trip.

Note the shape of every test: the pool or client is opened with `async with`
**inside the test body**, never handed over by a yield fixture. The client
owns an anyio task group and a task group must be exited by the task that
entered it; a yield fixture tears down in a different task and every test in
the file fails with "Attempted to exit cancel scope in a different task".
"""

from __future__ import annotations

import pytest
from mcp import Client

from mcp_host.catalog import open_pool
from mcp_host.connection import ServerSpec, open_connection
from mcp_host.interactive import (
    ScriptedResponder,
    as_elicitation_callback,
    call_with_input,
)
from mcp_host.loop import ToolLoop
from mcp_host.permissions import (
    Decision,
    PermissionGate,
    PermissionPolicy,
    always_allow,
    scripted_prompter,
)
from mcp_host.providers import ScriptedProvider
from mcp_host.results import read_result

from system_info import mcp as system_info_server


def spec(**kwargs):
    return ServerSpec.memory("system-info", system_info_server)


def declining(action="decline", content=None, seen=None):
    return as_elicitation_callback(ScriptedResponder(action, content, seen=seen))


# --------------------------------------------------------------------------
# Connecting and discovering
# --------------------------------------------------------------------------


async def test_connect_and_discover():
    async with open_connection(spec()) as conn:
        tools = await conn.list_tools()

        assert conn.server_info.name == "system-info"
        assert conn.protocol_version is not None
        assert {"get_system_info", "find_process", "terminate_process"} <= {
            t.name for t in tools
        }


def flatten(exc: BaseException) -> list[BaseException]:
    """Unwrap nested ExceptionGroups.

    An exception raised inside `async with Client(...)` escapes through an
    anyio task group, which re-raises it wrapped in an `ExceptionGroup`. So
    `pytest.raises(ZeroDivisionError)` does not match, and neither does a
    plain `except ZeroDivisionError` in host code. Worth knowing before you
    spend an afternoon on it.
    """
    nested = getattr(exc, "exceptions", None)
    if nested is None:
        return [exc]
    return [inner for e in nested for inner in flatten(e)]


async def test_the_connection_closes_cleanly_even_when_the_body_raises():
    """The lifetime guarantee, stated as a test.

    `AsyncExitStack` unwinds on the exception path too, and it unwinds *every*
    context it entered. The hand-rolled `__aenter__`/`__aexit__` pattern this
    replaces leaks the transport -- and with stdio, the subprocess -- whenever
    the session exit raises before the transport exit is reached.
    """
    captured = None
    with pytest.raises(BaseException) as excinfo:
        async with open_connection(spec()) as conn:
            captured = conn
            await conn.list_tools()
            raise ZeroDivisionError("boom")

    assert any(isinstance(e, ZeroDivisionError) for e in flatten(excinfo.value))
    # The connection really was torn down on the way out.
    outcome = await captured.call_tool("get_system_info", {})
    assert outcome.ok is False
    assert "closed" in outcome.for_model().lower()


async def test_resources_and_prompts_are_reachable_too():
    async with open_connection(spec()) as conn:
        resources = await conn.list_resources()
        prompts = await conn.list_prompts()

        assert any(str(r.uri) == "system://processes/top" for r in resources)
        assert any(p.name == "diagnose_performance" for p in prompts)

        read = await conn.read_resource("system://processes/top")
        assert read.contents[0].text.startswith("| PID |")


# --------------------------------------------------------------------------
# Calling a tool, and the isError branch over a real connection
# --------------------------------------------------------------------------


async def test_a_successful_call_carries_structured_content():
    async with open_connection(spec()) as conn:
        outcome = await conn.call_tool("get_system_info", {})

        assert outcome.ok is True
        assert 0 <= outcome.structured["cpu_percent"] <= 100
        assert outcome.blocks[0].kind == "text"


async def test_bad_arguments_come_back_as_a_failed_result_not_an_exception():
    """The defect, reproduced against a real server.

    `call_tool` returns normally. The failure is in `is_error`. A client that
    only wrapped this in try/except would feed the validation error to the
    model as the tool's answer.
    """
    async with open_connection(spec()) as conn:
        outcome = await conn.call_tool("find_process", {"limit": "not-a-number"})

        assert outcome.ok is False
        assert outcome.for_model().startswith("TOOL FAILED (find_process)")


async def test_an_unknown_tool_also_comes_back_as_a_failed_result():
    async with open_connection(spec()) as conn:
        outcome = await conn.call_tool("no_such_tool", {})
        assert outcome.ok is False


async def test_the_raw_result_would_have_looked_successful():
    """Same call, one level lower, to show what `read_result` is protecting
    against: the response is a perfectly ordinary CallToolResult."""
    async with Client(system_info_server) as client:
        raw = await client.call_tool("find_process", {"limit": "not-a-number"})

        assert raw.is_error is True
        assert raw.result_type == "complete"  # a *successful* response
        assert read_result("find_process", raw).ok is False


# --------------------------------------------------------------------------
# input_required: the MRTR loop from the client side
# --------------------------------------------------------------------------


async def test_the_sdk_drives_the_round_trip_when_given_a_callback():
    async with open_connection(
        spec(), elicitation_callback=declining("accept", {"confirm": False})
    ) as conn:
        outcome = await conn.call_tool("terminate_process", {"pid": 999999})

        assert outcome.ok is True
        assert "answered no" in outcome.text.lower()


async def test_declining_is_an_answer_not_an_error():
    async with open_connection(spec(), elicitation_callback=declining()) as conn:
        outcome = await conn.call_tool("terminate_process", {"pid": 999999})

        assert outcome.ok is True
        assert "declined" in outcome.text.lower()


async def test_without_an_elicitation_callback_the_call_fails_cleanly():
    """Passing a callback is what declares the capability.

    Connect without one and the server refuses before it ever asks, with
    -32021. That surfaces here as a raised MCPError, which the connection
    wrapper turns into a failed outcome rather than a crash.
    """
    async with open_connection(spec()) as conn:
        outcome = await conn.call_tool("terminate_process", {"pid": 999999})

        assert outcome.ok is False
        assert "elicitation" in outcome.for_model().lower()


async def test_driving_the_round_trip_by_hand():
    """The manual form: see the InputRequiredResult, answer it, call again."""
    responder = ScriptedResponder("accept", {"confirm": False})
    async with open_connection(
        spec(), elicitation_callback=declining("decline")
    ) as conn:
        result = await call_with_input(
            conn, "terminate_process", {"pid": 999999}, responder
        )

        assert result.is_error is not True
        assert "answered no" in result.content[0].text.lower()
        # The responder we passed to call_with_input answered, not the
        # connection-level callback: the manual loop bypasses it entirely.
        assert len(responder.seen) == 1
        assert "999999" in responder.seen[0]


async def test_the_question_is_asked_once_per_call():
    """A server that reworded its question between rounds would never
    converge; the loop would run to its cap. One question means the digest
    matched."""
    responder = ScriptedResponder("decline")
    async with open_connection(spec(), elicitation_callback=declining()) as conn:
        await call_with_input(conn, "terminate_process", {"pid": 4242}, responder)

    assert len(responder.seen) == 1


# --------------------------------------------------------------------------
# The tool loop, with the gate on the critical path
# --------------------------------------------------------------------------


async def test_the_loop_runs_a_tool_and_answers():
    plan = [[("get_system_info", {})], "The machine looks healthy."]
    async with open_pool([spec()]) as pool:
        gate = PermissionGate(always_allow())
        loop = ToolLoop(pool, gate, ScriptedProvider(plan))

        turn = await loop.run("How is my machine?")

        assert turn.text == "The machine looks healthy."
        assert turn.rounds == 2
        assert len(turn.results) == 1
        assert turn.results[0].is_error is False


async def test_read_only_tools_are_auto_approved_and_never_prompt():
    asked: list = []
    plan = [[("get_system_info", {})], "done"]
    async with open_pool([spec()]) as pool:
        gate = PermissionGate(scripted_prompter([Decision.DENY], asked=asked))
        loop = ToolLoop(pool, gate, ScriptedProvider(plan))

        turn = await loop.run("status?")

        assert turn.results[0].is_error is False
        assert asked == [], "a read-only tool should not have prompted"


async def test_a_denied_call_never_reaches_the_server():
    """The gate is on the critical path.

    `terminate_process` is annotated destructive, so it prompts; the prompter
    denies; the server is never called. The model is told, in a result whose
    text no server could have produced.
    """
    plan = [[("terminate_process", {"pid": 999999})], "I did not terminate anything."]
    async with open_pool([spec()], elicitation_callback=declining()) as pool:
        gate = PermissionGate(scripted_prompter([Decision.DENY]))
        loop = ToolLoop(pool, gate, ScriptedProvider(plan))

        turn = await loop.run("kill pid 999999")

        assert turn.results[0].is_error is True
        assert "permission denied" in turn.results[0].text.lower()
        assert turn.text == "I did not terminate anything."


async def test_an_approved_destructive_call_does_reach_the_server():
    """The mirror of the previous test: approval is not decorative either.

    The server then runs its own confirmation over MRTR, which the responder
    declines -- two independent gates, host and server, exactly as designed.
    """
    plan = [[("terminate_process", {"pid": 999999})], "The user declined."]
    async with open_pool([spec()], elicitation_callback=declining()) as pool:
        gate = PermissionGate(scripted_prompter([Decision.ALLOW_ONCE]))
        loop = ToolLoop(pool, gate, ScriptedProvider(plan))

        turn = await loop.run("kill pid 999999")

        assert turn.results[0].is_error is False
        assert "declined" in turn.results[0].text.lower()


async def test_parallel_tool_calls_all_run_and_all_come_back():
    plan = [
        [
            ("get_system_info", {}),
            ("find_process", {"name": "", "limit": 1}),
            ("find_process", {"name": "zzz", "limit": 1}),
        ],
        "Three answers.",
    ]
    async with open_pool([spec()]) as pool:
        loop = ToolLoop(pool, PermissionGate(always_allow()), ScriptedProvider(plan))

        turn = await loop.run("tell me everything")

        assert len(turn.results) == 3
        assert all(not r.is_error for r in turn.results)
        # Order preserved, so the tool_use ids line up on the way back.
        assert [r.call.name for r in turn.results] == [
            "get_system_info",
            "find_process",
            "find_process",
        ]


async def test_every_requested_call_gets_a_result_even_when_mixed():
    """One approved, one denied, one hallucinated. Three results, in order.

    Dropping any of them leaves a dangling tool_use id, which most providers
    reject outright.
    """
    plan = [
        [
            ("get_system_info", {}),
            ("terminate_process", {"pid": 1}),
            ("teleport", {}),
        ],
        "done",
    ]
    async with open_pool([spec()], elicitation_callback=declining()) as pool:
        gate = PermissionGate(scripted_prompter([Decision.DENY]))
        loop = ToolLoop(pool, gate, ScriptedProvider(plan))

        turn = await loop.run("do three things")

        assert [r.is_error for r in turn.results] == [False, True, True]
        assert "no such tool" in turn.results[2].text


async def test_a_failed_tool_result_is_labelled_for_the_model():
    plan = [[("find_process", {"limit": "nope"})], "That did not work."]
    async with open_pool([spec()]) as pool:
        loop = ToolLoop(pool, PermissionGate(always_allow()), ScriptedProvider(plan))

        turn = await loop.run("find things")

        assert turn.results[0].is_error is True
        assert turn.results[0].text.startswith("TOOL FAILED")


async def test_the_loop_stops_instead_of_calling_tools_forever():
    plan = [[("get_system_info", {})]] * 10
    async with open_pool([spec()]) as pool:
        loop = ToolLoop(
            pool, PermissionGate(always_allow()), ScriptedProvider(plan), max_rounds=3
        )

        turn = await loop.run("loop forever")

        assert turn.stopped_early is True
        assert turn.rounds == 3
        assert "Stopped after 3 tool rounds" in turn.text


async def test_the_provider_is_handed_the_catalog_names():
    """The names the model sees are the catalog's, not the servers' raw ones."""
    provider = ScriptedProvider(["nothing to do"])
    async with open_pool([spec()]) as pool:
        loop = ToolLoop(pool, PermissionGate(always_allow()), provider)
        await loop.run("hello")

    published = {t["name"] for t in provider.sent_tools}
    assert "get_system_info" in published
    assert all("input_schema" in t for t in provider.sent_tools)


async def test_the_history_survives_across_turns():
    async with open_pool([spec()]) as pool:
        provider = ScriptedProvider(["first answer", "second answer"])
        loop = ToolLoop(pool, PermissionGate(always_allow()), provider)

        first = await loop.run("hello")
        second = await loop.run("again")

        assert first.text == "first answer"
        assert second.text == "second answer"
        assert len(loop.messages) == 4  # user, assistant, user, assistant
