"""The smallest MCP Apps server that is still honest.

One tool, one `ui://` resource, one HTML widget, and a text answer that is
complete on its own. That last part is the requirement people skip: the Apps
specification says a UI-enabled tool **MUST** return a meaningful `content`
array whether or not the host can render anything.

## What the SDK gives you, and what it does not

`mcp` 2.0.0b2 ships the server half of MCP Apps at `mcp.server.apps`. That
module is real, introspectable API:

    from mcp.server.apps import Apps, APP_MIME_TYPE, client_supports_apps

`Apps` is an `Extension` (SEP-2133). It stamps `_meta.ui.resourceUri` on the
tools you register through it, serves the `ui://` resource with the
`text/html;profile=mcp-app` MIME type, and makes the server advertise
`io.modelcontextprotocol/ui` in its `server/discover` capabilities. It also
refuses, at server-construction time, to publish a tool whose `resourceUri`
has no matching resource.

What the Python SDK does **not** ship is the View half. There is no Python
equivalent of the JavaScript `App` class, because the View runs in a browser
iframe. `widget.html` therefore writes the postMessage dialect out by hand,
which the specification explicitly permits and which is about forty lines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.apps import Apps, ResourceCsp, client_supports_apps
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations

# Not `ctx.debug`. The MCP logging capability is deprecated as of 2026-07-28
# (SEP-2577) and the SDK warns if you use it; stderr is the replacement the
# deprecation notice names.
log = logging.getLogger("world-clock")

APP_URI = "ui://world-clock/app.html"
"""The URI the tool points at, and the URI the resource is registered under.

Any path shape works; hosts treat it as an opaque identifier. What matters is
the `ui://` scheme, which is reserved by the extension.
"""

WIDGET_HTML = (Path(__file__).parent / "widget.html").read_text(encoding="utf-8")

DEFAULT_ZONES = ["UTC", "America/New_York", "Europe/London", "Asia/Tokyo"]

# Reading a clock touches nothing. Annotations are hints for the host rather
# than enforcement, but they are what lets a host skip a confirmation prompt.
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)


@dataclass
class Reading:
    """One zone's current time."""

    zone: str
    local_time: str
    utc_offset: str


@dataclass
class ClockReadings:
    """What `world_clock` returns.

    This becomes `structuredContent` on the wire, and it is what the widget
    reads. The `content` array carries the same facts as prose, for the model
    and for hosts that cannot render a View.
    """

    captured_at: str
    readings: list[Reading]


apps = Apps()


def _read(zones: list[str]) -> ClockReadings:
    now = datetime.now(timezone.utc)
    readings: list[Reading] = []
    for zone in zones:
        try:
            local = now.astimezone(ZoneInfo(zone))
        except (ZoneInfoNotFoundError, ValueError):
            # An unknown zone is a bad argument, not a server fault. Say so in
            # the row rather than failing the whole call.
            readings.append(Reading(zone=zone, local_time="unknown zone", utc_offset=""))
            continue
        readings.append(
            Reading(
                zone=zone,
                local_time=local.strftime("%Y-%m-%d %H:%M"),
                utc_offset=local.strftime("%z") or "+0000",
            )
        )
    return ClockReadings(captured_at=now.strftime("%Y-%m-%d %H:%M"), readings=readings)


@apps.tool(resource_uri=APP_URI, title="World clock", annotations=READ_ONLY)
def world_clock(ctx: Context, zones: list[str] | None = None) -> ClockReadings:
    """Report the current time in several time zones.

    Args:
        zones: IANA time-zone names, such as `Europe/Berlin`. Defaults to a
            handful of common ones.
    """
    # Per-request branching, not startup branching. Under revision 2026-07-28
    # there is no connection-scoped negotiated state, so one server process
    # serves a UI-capable client and a text-only client on interleaved
    # requests. `client_supports_apps` reads this request's `_meta`.
    #
    # The branch here is deliberately small: the answer is identical either
    # way, and only the logging differs. A server whose answer changes shape
    # depending on the branch has made the UI load-bearing, which is the thing
    # graceful degradation forbids.
    log.info("MCP Apps negotiated on this request: %s", client_supports_apps(ctx))

    return _read(zones or DEFAULT_ZONES)


apps.add_html_resource(
    APP_URI,
    WIDGET_HTML,
    title="World clock",
    description="A table of current times, refreshable from inside the view.",
    # Nothing is fetched from anywhere. Declaring an empty policy is not the
    # same as declaring none: with `ui.csp` omitted the host applies its own
    # restrictive default, and being explicit documents the intent.
    csp=ResourceCsp(connect_domains=[], resource_domains=[]),
    prefers_border=True,
)

mcp = MCPServer(
    "world-clock",
    title="World clock",
    instructions=(
        "One tool, world_clock(zones), reporting the current time in the IANA "
        "time zones you name. The answer is complete in the text content; a "
        "host that supports MCP Apps additionally renders it as a small table."
    ),
    extensions=[apps],
)
