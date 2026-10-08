"""Post 05, section 10: `mcp install` pointed at a package, in a throwaway home directory.

Run from the series root:
    uv run python posts/05-first-server/snippets/mcp_install_sandbox.py

The SDK's `mcp install` writes into Claude Desktop's configuration under the
user's home directory. This script never touches the real one: it creates a
temporary home, creates the empty Claude configuration directory that the
command looks for there, and runs the command with USERPROFILE, HOME and
XDG_CONFIG_HOME pointing at that temporary home. It then runs the `mcp run`
half of the command the entry contains and prints the last line it logged.
The temporary directory is deleted on exit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "code" / "05-first-server" / "src" / "system_info" / "__main__.py"
MCP_CLI = [sys.executable, "-c", "from mcp.cli import app; app()"]


def claude_dir(home: Path) -> Path:
    """Where the SDK's mcp/cli/claude.py looks for Claude Desktop on each platform."""
    if sys.platform == "win32":
        return home / "AppData" / "Roaming" / "Claude"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude"
    return home / ".config" / "Claude"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        config_dir = claude_dir(home)
        config_dir.mkdir(parents=True)
        env = dict(
            os.environ,
            USERPROFILE=str(home),
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            PYTHONDONTWRITEBYTECODE="1",
        )

        relative = TARGET.relative_to(ROOT).as_posix()
        print(f"== mcp install {relative} ==")
        install = subprocess.run(MCP_CLI + ["install", str(TARGET)], env=env,
                                 capture_output=True, text=True, encoding="utf-8", timeout=90)
        print(f"exit code {install.returncode}; terminal output: {len(install.stdout + install.stderr)} characters")

        config = json.loads((config_dir / "claude_desktop_config.json").read_text(encoding="utf-8"))
        entries = config["mcpServers"]
        print(f"server entries written: {list(entries)}")
        print(json.dumps(entries, indent=2))

        print(f"\n== mcp run {relative}, the command that entry gives the host ==")
        run = subprocess.run(MCP_CLI + ["run", str(TARGET)], env=env,
                             capture_output=True, text=True, encoding="utf-8", timeout=90)
        last = (run.stderr.strip().splitlines() or ["(nothing on stderr)"])[-1]
        print(f"exit code {run.returncode}; last line on stderr:")
        print(last.strip())


if __name__ == "__main__":
    main()
