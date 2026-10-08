"""Post 10, sections 5 and 10: the project's command line, list and call.

Run from the series root:
    uv run python posts/10-mcp-client/snippets/cli_run.py

Runs the command line of code/10-mcp-client/ (mcp_host.cli.main) in this
process, with a temporary servers file whose one entry starts the server
under code/05-first-server/ as a stdio subprocess with this interpreter. It
runs `list`, then `call find_process` with a string where an integer
belongs, and prints each command's exit code. Inside code/10-mcp-client/ the
same commands are `uv run python -m mcp_host list` and
`uv run python -m mcp_host call ...`, against servers.example.json. The
subprocess is stopped when each command returns.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))

from mcp_host.cli import main  # noqa: E402

logging.disable(logging.WARNING)  # keep the output to what the command line prints


def run(config: Path, *argv: str) -> None:
    shown = " ".join(f"'{arg}'" if " " in arg else arg for arg in argv)
    print(f"$ mcp-host {shown}")
    code = main(["--config", str(config), *argv])
    print(f"(exit code {code})\n")


with tempfile.TemporaryDirectory() as tmp:
    config = Path(tmp) / "servers.json"
    entry = {
        "command": sys.executable,
        "args": ["-m", "system_info"],
        "cwd": str(SRC),
        "env": {"PYTHONDONTWRITEBYTECODE": "1"},
    }
    config.write_text(json.dumps({"mcpServers": {"system-info": entry}}), encoding="utf-8")

    run(config, "list")
    run(config, "call", "find_process", "--args", '{"limit": "not-a-number"}')
