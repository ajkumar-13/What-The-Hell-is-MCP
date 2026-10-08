"""Post 06, section 5: the return type that publishes no outputSchema, silently.

Run from the series root:
    uv run python posts/06-tools-in-depth/snippets/silent_schema.py

Prints the branch of the installed SDK that decides the matter, registers two
tools whose return classes differ only in where the attribute names are
written, lists and calls them on an in-memory server, shows the registration
error structured_output=True turns the silence into, and runs the project's
own schema assertion against the silent tool. Nothing is served over a
transport and nothing is left running.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass

from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities import func_metadata


class Records(logging.Handler):
    """Keep the server's log records, to show that there are none."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


LOG = Records()
logging.basicConfig(level=logging.DEBUG, handlers=[LOG])

mcp = MCPServer("silent-demo")


class Snapshot:
    """Looks like a fine return type. It is not."""
    def __init__(self, cpu_percent: float, memory_percent: float):
        self.cpu_percent = cpu_percent
        self.memory_percent = memory_percent

@dataclass
class AnnotatedSnapshot:
    """The same data, with class-body annotations."""
    cpu_percent: float
    memory_percent: float

@mcp.tool()
def silent() -> Snapshot:
    return Snapshot(cpu_percent=12.5, memory_percent=40.0)

@mcp.tool()
def loud() -> AnnotatedSnapshot:
    return AnnotatedSnapshot(cpu_percent=12.5, memory_percent=40.0)


def sdk_branch() -> list[str]:
    """The lines of the installed SDK that handle an ordinary class."""
    lines = inspect.getsource(func_metadata).splitlines()
    start = next(i for i, line in enumerate(lines) if "# Case 4" in line)
    end = next(i for i in range(start, len(lines)) if "model remains None" in lines[i])
    indent = len(lines[start]) - len(lines[start].lstrip())
    return [line[indent:] for line in lines[start : end + 1]]


async def main() -> None:
    print(f"== {func_metadata.__name__}, the branch for an ordinary class ==")
    print("\n".join(sdk_branch()))

    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        print("\n== tools/list ==")
        for name in ("silent", "loud"):
            print(f"{name:10} outputSchema = {json.dumps(tools[name].output_schema)}")

        print("\n== tools/call silent ==")
        result = await client.call_tool("silent", {})
        wire = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        wire.pop("_meta", None)
        print(json.dumps(wire, indent=2))

    sdk_records = [r for r in LOG.records if r.name.startswith("mcp") and r.levelno >= logging.INFO]
    print(f"\nSDK log records at INFO or above while registering, listing and calling: {len(sdk_records)}")

    print("\n== the same return type with structured_output=True ==")
    strict = MCPServer("strict-demo")
    try:
        @strict.tool(structured_output=True)
        def silent() -> Snapshot:  # noqa: F811 - a second server, the same mistake
            return Snapshot(cpu_percent=12.5, memory_percent=40.0)
        print("registered without error")
    except Exception as exc:  # noqa: BLE001 - printing the failure is the point
        print(f"{type(exc).__name__}: {exc}")

    print("\n== the project's schema assertion, run against this server ==")
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
        try:
            for tool in tools:
                assert tool.output_schema is not None, f"{tool.name} has no output schema"
            print("passed")
        except AssertionError as exc:
            print(f"AssertionError: {exc}")


asyncio.run(main())
