"""In-memory protocol tests for the MCP Apps server.

Every assertion here is about the *wire*, not about the widget: what a host
sees in `tools/list`, what it gets back from `resources/read`, and what the
server advertises in its capabilities. The rendering itself is a browser's job
and is not testable from Python.

As in every project in this series, the client is opened with `async with`
inside the test body. The client owns an anyio task group, and a task group has
to be exited by the task that entered it, so a yield fixture fails every test
with a cancel-scope error.
"""

from __future__ import annotations

import pytest
from mcp import Client
from mcp.client import advertise
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID, Apps
from mcp.server.mcpserver import MCPServer

from mcp_app_demo import APP_URI, mcp

# What a UI-capable host declares in its per-request `_meta`. The settings
# object is not decorative: `client_supports_apps` returns False for a client
# that names the extension but not the MIME type.
UI_CLIENT = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})


# --------------------------------------------------------------------------
# The tool-to-UI link
# --------------------------------------------------------------------------

async def test_the_tool_carries_a_ui_resource_uri():
    async with Client(mcp) as c:
        tool = next(t for t in (await c.list_tools()).tools if t.name == "world_clock")
        assert tool.meta is not None
        assert tool.meta["ui"] == {"resourceUri": APP_URI}


async def test_the_extension_is_advertised_in_capabilities():
    """A host reads this from `server/discover` before it asks for anything."""
    async with Client(mcp) as c:
        extensions = c.server_capabilities.extensions
        assert extensions is not None
        assert EXTENSION_ID in extensions


async def test_a_tool_bound_to_a_missing_resource_is_rejected():
    """The failure happens at construction, not at render time."""
    orphan = Apps()

    @orphan.tool(resource_uri="ui://nowhere/app.html")
    def broken() -> str:
        """A tool pointing at a resource nobody registered."""
        return "hello"

    with pytest.raises(ValueError, match="no such resource"):
        MCPServer("orphan", extensions=[orphan])


async def test_a_non_ui_scheme_is_rejected():
    with pytest.raises(ValueError, match="ui:// scheme"):
        Apps().add_html_resource("https://example.com/app.html", "<!DOCTYPE html>")


# --------------------------------------------------------------------------
# The ui:// resource
# --------------------------------------------------------------------------

async def test_the_widget_is_served_with_the_app_mime_type():
    async with Client(mcp) as c:
        contents = (await c.read_resource(APP_URI)).contents[0]
        assert contents.mime_type == APP_MIME_TYPE
        assert contents.text.startswith("<!DOCTYPE html>")


async def test_the_widget_declares_its_csp():
    async with Client(mcp) as c:
        resource = next(
            r for r in (await c.list_resources()).resources if str(r.uri) == APP_URI
        )
        assert resource.meta is not None
        assert resource.meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
        assert resource.meta["ui"]["prefersBorder"] is True


async def test_the_widget_is_self_contained():
    """The declared CSP allows no external origins, so nothing may be fetched."""
    async with Client(mcp) as c:
        html = (await c.read_resource(APP_URI)).contents[0].text
        assert "src=\"http" not in html
        assert "href=\"http" not in html


# --------------------------------------------------------------------------
# Graceful degradation: the same answer, with or without a host that renders
# --------------------------------------------------------------------------

async def test_a_text_only_client_gets_a_complete_answer():
    """No extension declared at all. The tool still answers."""
    async with Client(mcp) as c:
        result = await c.call_tool("world_clock", {"zones": ["UTC"]})
        assert result.is_error is not True
        assert result.content[0].text
        assert result.structured_content["readings"][0]["zone"] == "UTC"


async def test_a_ui_client_gets_byte_identical_structured_content():
    """The UI is presentation. Declaring it must not change the answer."""
    zones = ["UTC", "Asia/Tokyo"]

    async with Client(mcp) as c:
        plain = (await c.call_tool("world_clock", {"zones": zones})).structured_content

    async with Client(mcp, extensions=[UI_CLIENT]) as c:
        rich = (await c.call_tool("world_clock", {"zones": zones})).structured_content

    assert plain["readings"] == rich["readings"]


async def test_an_unknown_zone_is_a_row_not_a_failure():
    async with Client(mcp) as c:
        result = await c.call_tool("world_clock", {"zones": ["Mars/Olympus_Mons"]})
        assert result.is_error is not True
        assert result.structured_content["readings"][0]["local_time"] == "unknown zone"


async def test_a_known_zone_actually_resolves():
    """The counterpart to the test above, and the reason `tzdata` is a dependency.

    zoneinfo reads the system IANA database, and Windows has none. Without
    `tzdata` every zone falls into the "unknown zone" branch, including plain
    UTC -- and every other test here still passes, because they only check that
    the zone name is echoed back and that both clients agree. This one fails.
    """
    async with Client(mcp) as c:
        result = await c.call_tool("world_clock", {"zones": ["UTC", "Asia/Tokyo"]})
        readings = result.structured_content["readings"]

    for reading in readings:
        assert reading["local_time"] != "unknown zone", reading["zone"]
        assert reading["utc_offset"], reading["zone"]
