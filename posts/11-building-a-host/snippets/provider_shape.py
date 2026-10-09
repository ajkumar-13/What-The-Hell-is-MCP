"""Post 11, section 3: what the loop hands a provider, and the adapter that will not guess a model.

Run from the series root:
    uv run python posts/11-building-a-host/snippets/provider_shape.py

Opens the server under code/05-first-server/ in memory, builds the catalog
of code/10-mcp-client/, and prints the provider-format entry that
ScriptedProvider.format_tools makes for find_process, next to the same tool
in its wire spelling. Then it builds AnthropicProvider with MCP_HOST_MODEL
and ANTHROPIC_API_KEY removed from this process's environment, so it prints
the refusal without contacting anything, and runs the command line's
`chat --provider anthropic` the same way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp_host.catalog import open_pool  # noqa: E402
from mcp_host.cli import main  # noqa: E402
from mcp_host.connection import ServerSpec  # noqa: E402
from mcp_host.providers import AnthropicProvider, LLMProvider, ProviderUnavailable, ScriptedProvider  # noqa: E402

from system_info import mcp as system_info_server  # noqa: E402

logging.disable(logging.WARNING)  # keep the output to what is printed


async def show_format() -> None:
    async with open_pool([ServerSpec.memory("system-info", system_info_server)]) as pool:
        provider = ScriptedProvider([])
        print(f"ScriptedProvider satisfies LLMProvider: {isinstance(provider, LLMProvider)}")
        entries = provider.format_tools(pool.catalog)
        mine = next(e for e in entries if e["name"] == "find_process")
        print(f"format_tools entry for find_process, keys {sorted(mine)}:")
        print(f"  name: {mine['name']!r}")
        print(f"  description: {mine['description']!r}")
        print(f"  input_schema: {json.dumps(mine['input_schema'])}")
        wire = pool.catalog.resolve("find_process").tool.model_dump(by_alias=True, exclude_none=True)
        print(f"the same tool's wire keys: {sorted(wire)}")


asyncio.run(show_format())

for name in (AnthropicProvider.MODEL_ENV, AnthropicProvider.KEY_ENV):
    os.environ.pop(name, None)
print(f"\nAnthropicProvider.available(): {AnthropicProvider.available()}")
try:
    AnthropicProvider()
except ProviderUnavailable as exc:
    print(f"ProviderUnavailable: {exc}")
os.environ[AnthropicProvider.MODEL_ENV] = "any-model-id"
try:
    AnthropicProvider()
except ProviderUnavailable as exc:
    print(f"with {AnthropicProvider.MODEL_ENV} set: ProviderUnavailable: {exc}")
os.environ.pop(AnthropicProvider.MODEL_ENV)

# The command line's chat command, with neither variable set. It gives up
# before it opens any connection, so the servers file is never used.
print("\n$ mcp-host chat --provider anthropic")
with tempfile.TemporaryDirectory() as tmp:
    config = Path(tmp) / "servers.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    code = main(["--config", str(config), "chat", "--provider", "anthropic"])
print(f"(exit code {code})")
