"""Post 15, sections 1 and 4: the tools the first responder publishes, and where each lives.

Run from the series root:
    uv run python posts/15-devops-responder/snippets/tool_surface.py

Imports the package under code/15-devops-responder/src and connects an
in-memory client to it, as the project's tests do. No cluster is
needed for tools/list. Prints every tool with its arguments, its annotations,
whether it publishes an outputSchema and the module that defines it; then the
mutating Kubernetes API verbs found in each module's source.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

sys.path[:0] = [str(PROJECT / "src")]

import logging  # noqa: E402
from importlib.metadata import version  # noqa: E402

from mcp import Client  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import diagnose, inspect as inspect_tools, remediate  # noqa: E402

logging.getLogger().setLevel(logging.WARNING)

MUTATING = ("delete_namespaced", "patch_namespaced", "create_namespaced", "replace_namespaced")


def module_of(name: str) -> str:
    for module in (inspect_tools, diagnose, remediate):
        if inspect.isfunction(getattr(module, name, None)):
            return module.__name__.rsplit(".", 1)[-1] + ".py"
    return "?"


async def main() -> None:
    async with Client(k8s_responder.mcp) as client:
        tools = (await client.list_tools()).tools

    print(f"mcp {version('mcp')}, kubernetes {version('kubernetes')}, "
          f"Python {sys.version.split()[0]}")
    read = [t for t in tools if t.annotations.read_only_hint]
    print(f"{len(tools)} tools, {len(read)} with readOnlyHint true, "
          f"{sum(t.output_schema is not None for t in tools)} with an outputSchema")
    print()
    print(f"{'tool':<22}{'module':<14}{'readOnly':<10}{'destructive':<13}arguments (required first)")
    for t in tools:
        props = list(t.input_schema.get("properties", {}))
        required = t.input_schema.get("required", [])
        args = [f"{p}*" for p in props if p in required] + [p for p in props if p not in required]
        a = t.annotations
        print(f"{t.name:<22}{module_of(t.name):<14}{str(a.read_only_hint).lower():<10}"
              f"{str(a.destructive_hint).lower():<13}{', '.join(args)}")
    print("(* required)")
    print()

    print("mutating API verbs in each module's source:")
    for module in (inspect_tools, diagnose, remediate):
        source = inspect.getsource(module)
        found = [v for v in MUTATING if v in source]
        print(f"  {module.__name__.rsplit('.', 1)[-1] + '.py':<14}{', '.join(found) or 'none'}")


if __name__ == "__main__":
    asyncio.run(main())
