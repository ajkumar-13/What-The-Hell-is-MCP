# mcp-app-demo

The server from post 24: one tool that returns an interface.

A `world_clock` tool, a `ui://` resource carrying a self-contained HTML widget,
and a text answer that is complete without the widget. That is the whole of MCP
Apps at its smallest.

| Path | What it is |
|---|---|
| `src/mcp_app_demo/server.py` | The tool, the resource, and the `Apps` extension instance. |
| `src/mcp_app_demo/widget.html` | The View. The postMessage dialect written out by hand. |
| `tests/test_app.py` | Ten in-memory protocol tests. |

## Requirements

Python 3.10 or newer, and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run pytest
uv run python -m mcp_app_demo --http     # 127.0.0.1:8000/mcp
```

The `mcp` dependency is pinned exactly to `2.0.0b2`. That is the release that
implements protocol revision 2026-07-28 and the first one to ship
`mcp.server.apps`.

## What the SDK covers, and what it does not

`mcp` 2.0.0b2 ships the **server** half of MCP Apps:

```python
from mcp.server.apps import Apps, APP_MIME_TYPE, EXTENSION_ID, client_supports_apps
```

`Apps` is an `Extension` in the SEP-2133 sense. Passing it to
`MCPServer(extensions=[apps])` does four things: stamps `_meta.ui.resourceUri`
on every tool registered through `@apps.tool`, serves the `ui://` resource with
the `text/html;profile=mcp-app` MIME type, advertises
`io.modelcontextprotocol/ui` in the server's capabilities, and refuses at
construction time to publish a tool whose `resourceUri` has no matching
resource.

`client_supports_apps(ctx)` reads **this request's** `_meta` and returns `True`
only when the client named both the extension and the MIME type. There is no
connection-scoped negotiated state in this revision, so the check belongs
inside the handler and not at startup.

What the Python SDK does **not** ship is the **View** half. There is no Python
equivalent of the JavaScript `App` class from `@modelcontextprotocol/ext-apps`,
because the View is a browser document. `widget.html` therefore writes the
postMessage JSON-RPC dialect out by hand: a `post`/`request`/`notify` trio and
one `message` listener, about forty lines. The specification explicitly allows
this and calls the JavaScript class a convenience.

## The exchange

1. `tools/list` returns `world_clock` with `_meta.ui.resourceUri` set to
   `ui://world-clock/app.html`.
2. The host reads that resource with `resources/read` and gets an HTML5
   document with MIME type `text/html;profile=mcp-app`. It may prefetch it at
   connection time.
3. The model calls `world_clock`. The result carries both `content` (prose, for
   the model) and `structuredContent` (data, for the widget).
4. The host renders the widget in a sandboxed iframe, sends
   `ui/notifications/tool-input` and then `ui/notifications/tool-result` over
   `postMessage`, and the widget draws the table.
5. Pressing **Refresh** sends a `tools/call` from the widget to the host, which
   proxies it to this server and pushes the result back.

Step 4 is the only step this repository cannot test, because it needs a
browser. Everything else is asserted in `tests/test_app.py`.

## Testing it in a host

Serve it over HTTP, expose it, and add it as a custom connector:

```bash
uv run python -m mcp_app_demo --http
npx cloudflared tunnel --url http://localhost:8000
```

Then in Claude Desktop: **Settings > Connectors > Add > Add custom connector**,
and paste the tunnel URL with `/mcp` on the end. Custom connectors require a
paid plan. The `ext-apps` repository also ships an `examples/basic-host` you can
run locally against the same URL.

## Two things that are not in the specification yet

- **MCP Apps has not been updated for the stateless revision.** Neither the
  `2026-01-26` text nor the draft mentions `server/discover`, `2026-07-28`, or
  multi round-trip requests, and their negotiation examples still show an
  `initialize` handshake. The mechanism is unchanged; only the placement of the
  `extensions` map moved, and this project uses the current placement.
- **Apps combined with the tasks extension is unspecified.** Whether a UI-bound
  tool may return a `CreateTaskResult`, and what `ui/notifications/tool-result`
  does for a task that finishes later, is addressed by neither document.
