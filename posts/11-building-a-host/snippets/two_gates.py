"""Post 11, section 7: one destructive call, two independent gates.

Run from the series root:
    uv run python posts/11-building-a-host/snippets/two_gates.py

Runs the command line of code/10-mcp-client/ (mcp_host.cli.main) in this
process with a temporary servers file whose one entry starts the server
under code/05-first-server/ as a stdio subprocess with this interpreter, as
post 10's cli_run.py does. It runs `call terminate_process` for PID 999999,
which is not running, three times, with the answers already waiting on
standard input (so, as with piped input, they are not echoed): approve at
both prompts; deny at the host's prompt; and nothing at all. The subprocess
is stopped when each command returns.
"""

from __future__ import annotations

import io
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


def run(config: Path, answers: str) -> None:
    print(f"== answers waiting on standard input: {answers!r} ==")
    print("$ mcp-host call terminate_process --args '{\"pid\": 999999}'")
    real_stdin, sys.stdin = sys.stdin, io.StringIO(answers)
    try:
        code = main(["--config", str(config), "call", "terminate_process", "--args", '{"pid": 999999}'])
    finally:
        sys.stdin = real_stdin
    print(f"\n(exit code {code})\n")


with tempfile.TemporaryDirectory() as tmp:
    config = Path(tmp) / "servers.json"
    entry = {
        "command": sys.executable,
        "args": ["-m", "system_info"],
        "cwd": str(SRC),
        "env": {"PYTHONDONTWRITEBYTECODE": "1"},
    }
    config.write_text(json.dumps({"mcpServers": {"system-info": entry}}), encoding="utf-8")

    run(config, "y\ny\ny\n")
    run(config, "n\n")
    run(config, "")
