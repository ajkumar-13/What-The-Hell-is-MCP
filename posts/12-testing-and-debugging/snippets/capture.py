"""Post 12, section 10: generate the published surface of a server as Markdown, to commit and diff.

Run from the series root:
    uv run python posts/12-testing-and-debugging/snippets/capture.py
    uv run python posts/12-testing-and-debugging/snippets/capture.py > surface.md

Imports the first-server project under code/05-first-server/, opens an
in-memory client, and prints a Markdown table of every tool it publishes (the
input properties, whether an output schema exists, and two annotations), then
the resources, resource templates and prompts. Nothing in the output depends on
the machine's live state, so two runs on unchanged code print the same bytes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402

from system_info import mcp  # noqa: E402


def hint(value: bool | None) -> str:
    return "unset" if value is None else str(value).lower()


async def main() -> None:
    print("| Tool | Input properties | Output schema | readOnlyHint | destructiveHint |")
    print("|---|---|---|---|---|")
    async with Client(mcp) as c:
        for t in (await c.list_tools()).tools:
            props = ", ".join((t.input_schema or {}).get("properties", {})) or "none"
            out = "yes" if t.output_schema else "**null**"
            ann = t.annotations
            read_only = hint(ann.read_only_hint if ann else None)
            destructive = hint(ann.destructive_hint if ann else None)
            print(f"| `{t.name}` | {props} | {out} | {read_only} | {destructive} |")

        resources = [str(r.uri) for r in (await c.list_resources()).resources]
        templates = [t.uri_template for t in (await c.list_resource_templates()).resource_templates]
        prompts = [p.name for p in (await c.list_prompts()).prompts]
    print()
    print(f"Resources: {', '.join(f'`{u}`' for u in resources) or 'none'}")
    print(f"Resource templates: {', '.join(f'`{u}`' for u in templates) or 'none'}")
    print(f"Prompts: {', '.join(f'`{p}`' for p in prompts) or 'none'}")


asyncio.run(main())
