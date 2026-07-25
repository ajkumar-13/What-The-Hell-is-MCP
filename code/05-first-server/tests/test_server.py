"""In-memory protocol tests.

Post 12 explains the pattern. `Client(mcp)` connects straight to the server
object with no subprocess and no socket, so these run in milliseconds and are
still going through the real protocol machinery: schemas, validation, result
shapes, and the elicitation round trip.

Note the shape of every test: the client is opened with `async with` inside the
test body, not handed over by a yield fixture. The client owns an anyio task
group, and a task group has to be exited by the same task that entered it. A
yield fixture tears down in a different task and you get
"Attempted to exit cancel scope in a different task" on every test.
"""

from __future__ import annotations

import pytest
from mcp import Client
from mcp_types import ElicitResult, ResourceTemplateReference

from system_info import mcp


def answering(action: str, content: dict | None = None, seen: list | None = None):
    """A client whose user always responds to elicitation the same way."""

    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        return ElicitResult(action=action, content=content)

    return Client(mcp, elicitation_callback=callback)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

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


async def test_destructive_tool_is_marked_destructive():
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        ann = tools["terminate_process"].annotations
        assert ann.destructive_hint is True
        assert ann.read_only_hint is False


async def test_resolved_parameter_is_hidden_from_the_model():
    """The approval parameter must not appear in the published schema.

    If it did, a model could supply its own approval and the confirmation step
    would be decorative.
    """
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        props = tools["terminate_process"].input_schema["properties"]
        assert set(props) == {"pid"}


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

async def test_system_snapshot_is_structured():
    async with Client(mcp) as c:
        result = await c.call_tool("get_system_info", {})
        assert result.result_type == "complete"
        data = result.structured_content
        assert 0 <= data["cpu_percent"] <= 100
        assert 0 <= data["memory_percent"] <= 100
        assert data["memory_total_gb"] > 0


async def test_find_process_clamps_the_limit():
    async with Client(mcp) as c:
        result = await c.call_tool("find_process", {"name": "", "limit": 9999})
        assert result.structured_content["returned"] <= 50


async def test_find_process_reports_total_separately_from_returned():
    async with Client(mcp) as c:
        result = await c.call_tool("find_process", {"name": "", "limit": 1})
        data = result.structured_content
        assert data["returned"] <= 1
        assert data["total_matches"] >= data["returned"]


# --------------------------------------------------------------------------
# Resources and prompts
# --------------------------------------------------------------------------

async def test_static_resource_returns_markdown():
    async with Client(mcp) as c:
        result = await c.read_resource("system://processes/top")
        assert result.contents[0].text.startswith("| PID | Name | Memory (MB) |")


async def test_resource_template_resolves():
    async with Client(mcp) as c:
        result = await c.read_resource("system://disk/root")
        assert "disk: root" in result.contents[0].text


async def test_disk_outside_the_allowlist_is_rejected():
    """A template variable is untrusted input, and the allowlist is the defense."""
    async with Client(mcp) as c:
        with pytest.raises(Exception) as excinfo:
            await c.read_resource("system://disk/etc")
        assert "Unknown disk" in str(excinfo.value)


async def test_traversal_shaped_uri_does_not_match_the_template_at_all():
    """A URI with path separators fails to match before the handler is reached."""
    async with Client(mcp) as c:
        with pytest.raises(Exception) as excinfo:
            await c.read_resource("system://disk/../../etc/passwd")
        assert "Unknown resource" in str(excinfo.value)


async def test_prompt_embeds_a_live_reading():
    async with Client(mcp) as c:
        result = await c.get_prompt(
            "diagnose_performance", {"symptom": "high fan noise"}
        )
        text = result.messages[0].content.text
        assert "high fan noise" in text
        assert "CPU" in text


async def test_completion_filters_by_prefix():
    async with Client(mcp) as c:
        hit = await c.complete(
            ref=ResourceTemplateReference(uri="system://disk/{disk}"),
            argument={"name": "disk", "value": "r"},
        )
        assert "root" in hit.completion.values

        miss = await c.complete(
            ref=ResourceTemplateReference(uri="system://disk/{disk}"),
            argument={"name": "disk", "value": "zzz"},
        )
        assert miss.completion.values == []


# --------------------------------------------------------------------------
# Elicitation, the MRTR round trip
# --------------------------------------------------------------------------

async def test_declining_does_not_terminate():
    async with answering("decline") as c:
        result = await c.call_tool("terminate_process", {"pid": 999999})
        assert "declined" in result.content[0].text.lower()


async def test_cancelling_does_not_terminate():
    async with answering("cancel") as c:
        result = await c.call_tool("terminate_process", {"pid": 999999})
        assert "dismissed" in result.content[0].text.lower()


async def test_answering_no_does_not_terminate():
    async with answering("accept", {"confirm": False}) as c:
        result = await c.call_tool("terminate_process", {"pid": 999999})
        assert "answered no" in result.content[0].text.lower()


async def test_accepting_reaches_the_process_lookup():
    """Confirm the approved path runs, without killing anything real.

    pid 999999 will not exist, so the tool reaches its lookup and reports that
    rather than terminating a process on the machine running the tests.
    """
    async with answering("accept", {"confirm": True}) as c:
        result = await c.call_tool("terminate_process", {"pid": 999999})
        assert "no process" in result.content[0].text.lower()


async def test_the_question_is_asked_once_and_names_the_process():
    """The elicitation message must be derived only from the arguments.

    A question that varied between rounds would never match its recorded
    answer, and the call would never converge.
    """
    seen: list[str] = []
    async with answering("decline", seen=seen) as c:
        await c.call_tool("terminate_process", {"pid": 4242})

    assert len(seen) == 1
    assert "4242" in seen[0]


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------

async def test_watch_cpu_returns_one_sample_per_second():
    async with Client(mcp) as c:
        result = await c.call_tool("watch_cpu", {"seconds": 2})
        data = result.structured_content
        assert data["seconds"] == 2
        assert len(data["samples"]) == 2
        assert data["peak_percent"] >= data["average_percent"]
