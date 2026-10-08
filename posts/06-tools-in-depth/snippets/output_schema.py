"""Post 06, section 4: which return annotations publish an outputSchema, and who checks it.

Run from the series root:
    uv run python posts/06-tools-in-depth/snippets/output_schema.py

Registers one throwaway tool per kind of return annotation on an in-memory
server and prints what each publishes as outputSchema. Then calls a tool that
builds a top-level array by hand, and shows the two checks a published schema
gets: the server's own check of a hand-built result, and the client's check of
whatever arrives. Nothing is served over a transport and nothing is left
running.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from dataclasses import dataclass
from typing import Annotated, TypedDict

from mcp import Client
from mcp.server.mcpserver import Image, MCPServer
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel


class Records(logging.Handler):
    """Keep the server's log records instead of printing them."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


LOG = Records()
logging.basicConfig(level=logging.INFO, handlers=[LOG])


class AsModel(BaseModel):
    cpu_percent: float


class AsTypedDict(TypedDict):
    cpu_percent: float


@dataclass
class AsDataclass:
    cpu_percent: float


class AsAnnotatedClass:
    cpu_percent: float

    def __init__(self, cpu_percent: float) -> None:
        self.cpu_percent = cpu_percent


@dataclass
class Reading:
    value: float
    units: str


@dataclass
class Celsius:
    value: float


mcp = MCPServer("return-types")

ROWS = [
    ("BaseModel subclass", AsModel),
    ("TypedDict", AsTypedDict),
    ("dataclass", AsDataclass),
    ("class with class-body annotations", AsAnnotatedClass),
    ("str", str),
    ("None", None),
    ("dict[str, int]", dict[str, int]),
    ("dict[int, int]", dict[int, int]),
    ("list[str]", list[str]),
    ("int | None", int | None),
    ("TextContent (a content block)", TextContent),
    ("Image (the SDK helper)", Image),
    ("CallToolResult", CallToolResult),
]

for index, (_, annotation) in enumerate(ROWS):
    def fn():  # noqa: ANN202 - the annotation is set below, per row
        return None

    fn.__name__ = f"t{index:02d}"
    fn.__annotations__ = {"return": annotation}
    mcp.tool()(fn)


@mcp.tool()
def t99():  # no return annotation at all
    return None


@mcp.tool(structured_output=False)
def t98() -> AsDataclass:  # a schema-worthy type, switched off
    return AsDataclass(cpu_percent=0.0)


@mcp.tool(structured_output=True)
def t97() -> TextContent:  # a content block, switched on
    return TextContent(type="text", text="hi")


@mcp.tool()
def letters() -> list[str]:
    """A list return, wrapped by the SDK."""
    return ["a", "b"]


@mcp.tool()
def raw_array() -> CallToolResult:
    """Top-level array structuredContent, built by hand."""
    return CallToolResult(
        content=[TextContent(type="text", text='[{"id": "1"}, {"id": "2"}]')],
        structured_content=[{"id": "1"}, {"id": "2"}],
    )


@mcp.tool()
def reading() -> Annotated[CallToolResult, Reading]:
    """A hand-built result that leaves out a field its own schema requires."""
    return CallToolResult(
        content=[TextContent(type="text", text="21.5")],
        structured_content={"value": 21.5},
    )


@mcp.tool()
def celsius() -> Celsius:
    """A conforming result; its published schema is corrupted below."""
    return Celsius(value=21.5)


def describe(schema) -> str:
    if schema is None:
        return "outputSchema: none"
    props = schema.get("properties", {})
    if set(props) == {"result"} and schema.get("title", "").endswith("Output"):
        return 'wrapped as {"result": ...}'
    return f'unwrapped, title {json.dumps(schema.get("title"))}'


async def main() -> None:
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        print("== return annotation -> published outputSchema ==")
        for index, (label, _) in enumerate(ROWS):
            print(f"{label:36} {describe(tools[f't{index:02d}'].output_schema)}")
        print(f"{'(no annotation)':36} {describe(tools['t99'].output_schema)}")
        print(f"{'dataclass, structured_output=False':36} {describe(tools['t98'].output_schema)}")
        print(f"{'TextContent, structured_output=True':36} {describe(tools['t97'].output_schema)}")

        print("\n== tools/call letters, annotated -> list[str] ==")
        result = await client.call_tool("letters", {})
        print(f"structuredContent: {json.dumps(result.structured_content)}")

        print("\n== tools/call raw_array ==")
        result = await client.call_tool("raw_array", {})
        print(f"structuredContent: {json.dumps(result.structured_content)}")
        print(f"outputSchema:      {json.dumps(tools['raw_array'].output_schema)}")

        print("\n== the server checks a hand-built result against its schema ==")
        result = await client.call_tool("reading", {})
        print(f"isError: {json.dumps(result.is_error)}, text: {result.content[0].text!r}")
        for record in LOG.records:
            if record.levelno >= logging.ERROR:
                print(f"server log: {record.levelname} {record.getMessage()}")

    # Corrupt the schema the server publishes for `celsius`, so that the data it
    # really returns no longer fits: the published copy now requires `units`.
    # This reaches into the SDK's private tool table, which is only acceptable in
    # a demonstration.
    tool = mcp._tool_manager.get_tool("celsius")
    corrupted = copy.deepcopy(tool.output_schema)
    corrupted["properties"]["units"] = {"type": "string"}
    corrupted["required"] = ["value", "units"]
    tool.__dict__["output_schema"] = corrupted

    print("\n== the client checks every result against the published schema ==")
    async with Client(mcp) as client:
        await client.list_tools()
        try:
            await client.call_tool("celsius", {})
            print("no error")
        except RuntimeError as exc:
            print(f"RuntimeError: {str(exc).splitlines()[0]}")


asyncio.run(main())
