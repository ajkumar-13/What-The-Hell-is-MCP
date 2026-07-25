"""Many servers, one catalog.

Post 11. A host connects to several servers and has to present their tools to
a model as one flat list, because that is the only shape a provider's tool API
takes. Two servers exporting `search` is not hypothetical; it is what happens
the moment you connect a filesystem server and a docs server.

**The separator is a dot.** Tool names are constrained: the wire allows
letters, digits, underscore, dot and hyphen, and nothing else. A slash is
illegal, so `github/search` is not an option however natural it reads. Some
provider APIs narrow the set further -- Anthropic's and OpenAI's tool names
are `[A-Za-z0-9_-]` only -- which is why `separator` is a parameter and not a
constant. Pass `separator="_"` when you are talking to one of those.

**Namespacing is applied on collision, not always.** A tool whose name is
unique across every connected server keeps its bare name, because that is what
the server's own documentation calls it and what the model has most likely
seen. A contested name gets qualified on *both* sides -- never one -- so no
server silently wins. Every tool is additionally always resolvable by its
qualified name, so a model that saw `system-info.find_process` in an earlier
turn still resolves it after another server disconnects and the collision
goes away.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Iterator, Sequence

from mcp_types import Tool

from .connection import ServerConnection, ServerSpec, open_connection
from .results import ToolOutcome

# The character class the spec allows in a tool name. Worth enforcing at the
# catalog boundary: a server that ships an illegal name should fail here,
# loudly, rather than at the provider's API with a validation error that names
# a field the reader has never seen.
_LEGAL_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


class UnknownTool(KeyError):
    """The model asked for a tool this catalog does not have."""


@dataclass(frozen=True)
class CatalogEntry:
    """One tool, plus which server it came from and what we call it."""

    name: str
    server: str
    tool_name: str
    tool: Tool

    @property
    def qualified_name(self) -> str:
        return f"{self.server}.{self.tool_name}"

    @property
    def description(self) -> str:
        return self.tool.description or ""

    @property
    def annotations(self) -> Any:
        return self.tool.annotations


class ToolCatalog:
    """The flat, de-collided view of every connected server's tools."""

    def __init__(self, entries: Sequence[CatalogEntry], aliases: dict[str, str]) -> None:
        self._entries = tuple(entries)
        self._by_name = {e.name: e for e in entries}
        self._aliases = dict(aliases)

    @classmethod
    def build(
        cls,
        tools_by_server: dict[str, Iterable[Tool]],
        *,
        separator: str = ".",
    ) -> ToolCatalog:
        counts: dict[str, int] = defaultdict(int)
        for tools in tools_by_server.values():
            for tool in tools:
                counts[tool.name] += 1

        entries: list[CatalogEntry] = []
        aliases: dict[str, str] = {}
        for server, tools in tools_by_server.items():
            for tool in tools:
                contested = counts[tool.name] > 1
                name = (
                    f"{server}{separator}{tool.name}" if contested else tool.name
                )
                if not _LEGAL_NAME.match(name):
                    raise ValueError(
                        f"tool name {name!r} from server {server!r} is not a legal "
                        "MCP tool name (letters, digits, '_', '.', '-' only)"
                    )
                entry = CatalogEntry(
                    name=name, server=server, tool_name=tool.name, tool=tool
                )
                entries.append(entry)
                # Always resolvable by the fully qualified form, whether or not
                # it is the published name.
                aliases.setdefault(f"{server}{separator}{tool.name}", name)
                aliases.setdefault(f"{server}.{tool.name}", name)
        return cls(entries, aliases)

    def resolve(self, name: str) -> CatalogEntry:
        entry = self._by_name.get(name)
        if entry is not None:
            return entry
        aliased = self._aliases.get(name)
        if aliased is not None:
            return self._by_name[aliased]
        raise UnknownTool(name)

    def names(self) -> list[str]:
        return [e.name for e in self._entries]

    def servers(self) -> list[str]:
        seen: list[str] = []
        for entry in self._entries:
            if entry.server not in seen:
                seen.append(entry.server)
        return seen

    def for_server(self, server: str) -> list[CatalogEntry]:
        return [e for e in self._entries if e.server == server]

    def __iter__(self) -> Iterator[CatalogEntry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name or name in self._aliases


class ServerPool:
    """Every open connection, plus the catalog built from them."""

    def __init__(self, connections: Sequence[ServerConnection]) -> None:
        self._connections = {c.name: c for c in connections}
        self.catalog = ToolCatalog.build({})

    @property
    def connections(self) -> dict[str, ServerConnection]:
        return dict(self._connections)

    async def refresh(self, *, separator: str = ".") -> ToolCatalog:
        """Re-list every server, concurrently, and rebuild the catalog.

        Concurrent because a cold stdio server can take a second to start and
        four of them in sequence is four seconds of a host that looks hung.
        """
        names = list(self._connections)
        listings = await asyncio.gather(
            *(self._connections[n].list_tools() for n in names)
        )
        self.catalog = ToolCatalog.build(dict(zip(names, listings)), separator=separator)
        return self.catalog

    async def call(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolOutcome:
        """Route one call to the server that owns it.

        An unknown name is a `ToolOutcome`, not an exception: a model that
        hallucinated a tool should be told so and given another turn, not
        crash the host.
        """
        try:
            entry = self.catalog.resolve(name)
        except UnknownTool:
            known = ", ".join(sorted(self.catalog.names())) or "(none)"
            return ToolOutcome.failed(
                name, f"no such tool. Available tools: {known}"
            )
        conn = self._connections[entry.server]
        return await conn.call_tool(entry.tool_name, arguments or {})


@asynccontextmanager
async def open_pool(
    specs: Sequence[ServerSpec],
    *,
    elicitation_callback: Any = None,
    separator: str = ".",
) -> AsyncIterator[ServerPool]:
    """Open every server, build the catalog, and close everything on the way out.

    One `AsyncExitStack` holds all of them. If the third server fails to start,
    the first two are still shut down properly; if a shutdown raises, the rest
    still run. Entered and exited by the same task, which is the rule the whole
    connection module is built around.
    """
    async with AsyncExitStack() as stack:
        connections = [
            await stack.enter_async_context(
                open_connection(spec, elicitation_callback=elicitation_callback)
            )
            for spec in specs
        ]
        pool = ServerPool(connections)
        await pool.refresh(separator=separator)
        yield pool
