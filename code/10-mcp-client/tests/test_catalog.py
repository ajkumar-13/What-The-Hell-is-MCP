"""Many servers, one catalog. Post 11.

The first half is pure: `ToolCatalog.build` takes tool lists and produces
names, so it can be tested without a connection. The second half opens two
real servers in memory and checks that a call actually routes to the right
one -- the naming scheme is only correct if dispatch agrees with it.

The second server is defined here rather than imported. Collisions are the
whole subject, and manufacturing one takes eight lines.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer
from mcp_types import Tool, ToolAnnotations

from mcp_host.catalog import ToolCatalog, UnknownTool, open_pool
from mcp_host.connection import ServerSpec

from system_info import mcp as system_info_server

# --------------------------------------------------------------------------
# A second server, whose `find_process` collides with system-info's
# --------------------------------------------------------------------------

mini = MCPServer("mini")


@mini.tool(
    title="Find a process (fake)",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
def find_process(name: str, limit: int = 5) -> str:
    """A deliberately different find_process, to force a name collision."""
    return f"mini found nothing matching {name!r}"


@mini.tool(annotations=ToolAnnotations(read_only_hint=True))
def ping() -> str:
    """Return pong."""
    return "pong"


def tool(name: str, **kwargs) -> Tool:
    return Tool(name=name, input_schema={"type": "object"}, **kwargs)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


def test_a_unique_name_is_left_alone():
    """The server's own documentation calls it `get_system_info`. So do we."""
    catalog = ToolCatalog.build(
        {"system-info": [tool("get_system_info")], "docs": [tool("search")]}
    )
    assert sorted(catalog.names()) == ["get_system_info", "search"]


def test_a_collision_namespaces_both_sides():
    """Never one. Whichever server we listed first must not silently win."""
    catalog = ToolCatalog.build(
        {"docs": [tool("search")], "files": [tool("search")]}
    )
    assert sorted(catalog.names()) == ["docs.search", "files.search"]


def test_the_separator_is_a_dot_because_a_slash_is_illegal():
    catalog = ToolCatalog.build({"a": [tool("x")], "b": [tool("x")]})
    assert "a.x" in catalog.names()
    assert not any("/" in n for n in catalog.names())


def test_an_illegal_tool_name_is_rejected_at_the_catalog():
    """Better to fail here, naming the server, than at a provider's API with a
    validation error about a field the reader has never seen."""
    with pytest.raises(ValueError, match="not a legal MCP tool name"):
        ToolCatalog.build({"bad": [tool("group/search")]})


def test_a_provider_with_a_narrower_charset_can_change_the_separator():
    """Some provider tool-name rules are [A-Za-z0-9_-] only, with no dot."""
    catalog = ToolCatalog.build(
        {"docs": [tool("search")], "files": [tool("search")]}, separator="_"
    )
    assert sorted(catalog.names()) == ["docs_search", "files_search"]


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_a_qualified_name_resolves_even_when_it_is_not_the_published_name():
    """A model that saw `system-info.get_system_info` in an earlier turn must
    still resolve it after the colliding server disconnects."""
    catalog = ToolCatalog.build({"system-info": [tool("get_system_info")]})

    entry = catalog.resolve("system-info.get_system_info")

    assert entry.name == "get_system_info"
    assert entry.server == "system-info"


def test_resolution_carries_the_server_and_the_original_tool_name():
    catalog = ToolCatalog.build({"docs": [tool("search")], "files": [tool("search")]})
    entry = catalog.resolve("files.search")

    assert entry.server == "files"
    # What we send on the wire is the server's own name, never the namespaced
    # one. The server has never heard of `files.search`.
    assert entry.tool_name == "search"


def test_an_unknown_name_raises_unknown_tool():
    catalog = ToolCatalog.build({"docs": [tool("search")]})
    with pytest.raises(UnknownTool):
        catalog.resolve("nope")


def test_annotations_survive_into_the_catalog():
    """The gate reads them from here, so losing them here disables the gate's
    read-only shortcut for every tool."""
    catalog = ToolCatalog.build(
        {"s": [tool("t", annotations=ToolAnnotations(read_only_hint=True))]}
    )
    assert catalog.resolve("t").annotations.read_only_hint is True


# --------------------------------------------------------------------------
# Dispatch, against two real servers
# --------------------------------------------------------------------------


async def test_two_servers_produce_one_catalog():
    specs = [
        ServerSpec.memory("system-info", system_info_server),
        ServerSpec.memory("mini", mini),
    ]
    async with open_pool(specs) as pool:
        names = set(pool.catalog.names())

        # find_process exists on both, so both are namespaced.
        assert "system-info.find_process" in names
        assert "mini.find_process" in names
        assert "find_process" not in names
        # ping only exists on mini, so it keeps its bare name.
        assert "ping" in names
        assert pool.catalog.servers() == ["system-info", "mini"]


async def test_a_namespaced_call_reaches_the_right_server():
    """The naming scheme is only correct if dispatch agrees with it."""
    specs = [
        ServerSpec.memory("system-info", system_info_server),
        ServerSpec.memory("mini", mini),
    ]
    async with open_pool(specs) as pool:
        theirs = await pool.call("mini.find_process", {"name": "zzz"})
        ours = await pool.call("system-info.find_process", {"name": "zzz", "limit": 1})

        assert theirs.ok and ours.ok
        assert "mini found nothing" in theirs.text
        assert ours.structured is not None
        assert ours.structured["query"] == "zzz"


async def test_an_unknown_tool_is_a_result_not_an_exception():
    """A model that invented a tool name should get another turn, not take the
    host down with it."""
    specs = [ServerSpec.memory("mini", mini)]
    async with open_pool(specs) as pool:
        outcome = await pool.call("teleport", {})

        assert outcome.ok is False
        assert "no such tool" in outcome.for_model()
        assert "ping" in outcome.for_model()


async def test_parallel_calls_across_servers_all_complete():
    import asyncio

    specs = [
        ServerSpec.memory("system-info", system_info_server),
        ServerSpec.memory("mini", mini),
    ]
    async with open_pool(specs) as pool:
        outcomes = await asyncio.gather(
            pool.call("ping", {}),
            pool.call("mini.find_process", {"name": "a"}),
            pool.call("system-info.find_process", {"name": "", "limit": 1}),
        )
        assert all(o.ok for o in outcomes)
        assert outcomes[0].text == "pong"
