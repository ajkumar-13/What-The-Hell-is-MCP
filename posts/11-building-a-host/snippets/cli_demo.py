"""Post 11, sections 9 and 10: the command line's demo, the whole loop with no key.

Run from the series root:
    uv run python posts/11-building-a-host/snippets/cli_demo.py

Runs `demo` from the command line of code/10-mcp-client/ (mcp_host.cli.main)
in this process, with a temporary servers file whose one entry starts the
server under code/05-first-server/ as a stdio subprocess with this
interpreter. The demo's model is ScriptedProvider, with a plan built from
the first two read-only tools that need no arguments, so nothing contacts a
model provider. Inside code/10-mcp-client/ the same command is
`uv run python -m mcp_host demo`, against servers.example.json. The script
prints the demo's output, with the measured values left in, and then the
wall-clock time of the whole command. The subprocess is stopped when the
command returns.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))

from mcp_host.cli import main  # noqa: E402

logging.disable(logging.WARNING)  # keep the output to what the command line prints

with tempfile.TemporaryDirectory() as tmp:
    config = Path(tmp) / "servers.json"
    entry = {
        "command": sys.executable,
        "args": ["-m", "system_info"],
        "cwd": str(SRC),
        "env": {"PYTHONDONTWRITEBYTECODE": "1"},
    }
    config.write_text(json.dumps({"mcpServers": {"system-info": entry}}), encoding="utf-8")

    print("$ mcp-host demo")
    start = time.perf_counter()
    code = main(["--config", str(config), "demo"])
    elapsed = time.perf_counter() - start
    print(f"\n(exit code {code}; the command took {elapsed:.1f} s, watch_cpu's default is 5 seconds)")
