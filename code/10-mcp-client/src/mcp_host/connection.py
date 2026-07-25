"""Connecting to a server, and letting go of it cleanly.

Post 10. Three transports, one lifetime rule.

The lifetime rule is the part worth reading twice. The v2 `Client` is an async
context manager wrapping an anyio task group, and **a task group must be
exited by the task that entered it**. Drive it with `async with`, or with an
`AsyncExitStack` entered and exited in the same task. Do not call
`__aenter__` / `__aexit__` by hand and store the object on `self`, which is
what a lot of 1.x-era client code does:

    self._transport_cm = stdio_client(params)          # DON'T
    self._read, self._write = await self._transport_cm.__aenter__()
    self.session = ClientSession(self._read, self._write)
    await self.session.__aenter__()

That has two bugs, and they compound. The first is that `close()` runs in
whatever task happens to call it, and you get "Attempted to exit cancel scope
in a different task". The second is that the paired `close()` almost always
looks like this:

    await self.session.__aexit__(None, None, None)
    await self._transport_cm.__aexit__(None, None, None)

If the session exit raises, the transport exit never runs, and the subprocess
that `close()` existed to reap is leaked. `AsyncExitStack` unwinds every
context it entered even when an inner one raises, which is precisely the
guarantee you want here.
"""

from __future__ import annotations

import json
import pathlib
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import GetPromptResult, ReadResourceResult, Tool

from .results import ToolOutcome, read_result

Transport = Literal["stdio", "http", "memory"]


@dataclass
class ServerSpec:
    """Everything needed to reach one server, before any connection exists."""

    name: str
    transport: Transport = "stdio"

    # stdio
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None

    # http
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    # memory: an MCPServer (or low-level Server) object in this process.
    # No subprocess, no socket. This is what the tests use, and post 12
    # explains why it is also the right way to test a server.
    server: Any = None

    @classmethod
    def stdio(cls, name: str, command: str, *args: str, **kwargs: Any) -> ServerSpec:
        return cls(name=name, transport="stdio", command=command, args=list(args), **kwargs)

    @classmethod
    def http(cls, name: str, url: str, **kwargs: Any) -> ServerSpec:
        return cls(name=name, transport="http", url=url, **kwargs)

    @classmethod
    def memory(cls, name: str, server: Any) -> ServerSpec:
        return cls(name=name, transport="memory", server=server)

    @classmethod
    def from_config(cls, name: str, entry: dict[str, Any]) -> ServerSpec:
        """Parse one entry of the `mcpServers` object hosts have standardised on.

        Two shapes: `{"command": ..., "args": [...]}` for stdio, and
        `{"url": ...}` for Streamable HTTP.
        """
        if "url" in entry:
            return cls.http(
                name,
                entry["url"],
                headers=dict(entry.get("headers") or {}),
            )
        if "command" not in entry:
            raise ValueError(f"server {name!r}: needs either 'command' or 'url'")
        return cls(
            name=name,
            transport="stdio",
            command=entry["command"],
            args=list(entry.get("args") or []),
            env=dict(entry["env"]) if entry.get("env") else None,
            cwd=entry.get("cwd"),
        )


def load_config(path: str | pathlib.Path) -> list[ServerSpec]:
    """Read a `{"mcpServers": {...}}` file into specs."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or data.get("servers") or {}
    return [ServerSpec.from_config(name, entry) for name, entry in servers.items()]


class ServerConnection:
    """A live client, wrapped so a host never touches raw result types.

    The wrapper is thin on purpose. Its whole job is that `call_tool` returns
    a `ToolOutcome` rather than a `CallToolResult`, so a caller cannot forget
    to check `is_error`. See results.py.
    """

    def __init__(self, spec: ServerSpec, client: Client) -> None:
        self.spec = spec
        self.client = client

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def server_info(self) -> Any:
        return self.client.server_info

    @property
    def protocol_version(self) -> str | None:
        return self.client.protocol_version

    async def list_tools(self) -> list[Tool]:
        return list((await self.client.list_tools()).tools)

    async def list_resources(self) -> list[Any]:
        return list((await self.client.list_resources()).resources)

    async def list_prompts(self) -> list[Any]:
        return list((await self.client.list_prompts()).prompts)

    async def read_resource(self, uri: str) -> ReadResourceResult:
        return await self.client.read_resource(uri)

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        return await self.client.get_prompt(name, arguments)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolOutcome:
        """Call a tool and come back with something safe to branch on.

        Both failure modes are folded into `ToolOutcome.ok`:

        - the call raised, meaning the transport or the protocol failed;
        - the call returned with `is_error`, meaning the tool itself failed.

        The broad `except Exception` is deliberate and is the one place in
        this project where one belongs. A host that dies because one tool call
        blew up is worse than a host that tells the model the call blew up.
        """
        try:
            result = await self.client.call_tool(name, arguments or {})
        except Exception as exc:  # noqa: BLE001 - see docstring
            return ToolOutcome.failed(name, f"{type(exc).__name__}: {exc}")
        return read_result(name, result)


@asynccontextmanager
async def open_connection(
    spec: ServerSpec,
    *,
    elicitation_callback: Any = None,
    read_timeout_seconds: float | None = None,
) -> AsyncIterator[ServerConnection]:
    """Connect, yield, and tear down in the same task.

    Use it as a context manager and the lifetime problem disappears:

        async with open_connection(spec) as conn:
            tools = await conn.list_tools()

    `read_timeout_seconds` is a float of seconds in v2, not a `timedelta`.
    """
    async with AsyncExitStack() as stack:
        transport = await _transport_for(spec, stack)
        client = await stack.enter_async_context(
            Client(
                transport,
                elicitation_callback=elicitation_callback,
                read_timeout_seconds=read_timeout_seconds,
            )
        )
        yield ServerConnection(spec, client)


async def _transport_for(spec: ServerSpec, stack: AsyncExitStack) -> Any:
    """Build the argument `Client(...)` wants, by transport.

    `Client` dispatches on the shape of what it is given: an `MCPServer` runs
    in process, a `str` is always an HTTP URL (there is no `stdio://` form),
    and anything else is used as a transport.
    """
    if spec.transport == "memory":
        if spec.server is None:
            raise ValueError(f"server {spec.name!r}: memory transport needs a server object")
        return spec.server

    if spec.transport == "http":
        if not spec.url:
            raise ValueError(f"server {spec.name!r}: http transport needs a url")
        if not spec.headers:
            # The plain form. `Client("https://...")` would also work; going
            # through the transport keeps one code path.
            return streamable_http_client(spec.url)
        # Headers, auth, and timeouts need a client you built. Note the
        # import: v2 ships httpx2, and `import httpx` here would give you a
        # different library whose exceptions the SDK never raises.
        import httpx2

        http_client = await stack.enter_async_context(
            httpx2.AsyncClient(headers=spec.headers, timeout=30.0)
        )
        return streamable_http_client(spec.url, http_client=http_client)

    if not spec.command:
        raise ValueError(f"server {spec.name!r}: stdio transport needs a command")
    params = StdioServerParameters(
        command=spec.command,
        args=spec.args,
        env=spec.env,
        cwd=spec.cwd,
    )
    return stdio_client(params)
