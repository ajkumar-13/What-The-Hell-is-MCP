"""Post 05, section 2: why the pre-release setting belongs in pyproject.toml.

Run from the series root:
    uv run python posts/05-first-server/snippets/uv_prerelease.py

Everything runs offline (`--offline`) against uv's local cache, which already
holds mcp 2.0.0b2 once the series root has been synced, inside a temporary
directory that is deleted on exit. It shows, in order: the resolver refusing
the pinned pre-release without the flag; a project created with the flag; the
next plain `uv lock` dropping the recorded mode; and the same project with the
mode written into pyproject.toml, locked from scratch. If uv or its cache is
not available, it says so and stops.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PIN = "mcp[cli]==2.0.0b2"
UV = os.environ.get("UV") or shutil.which("uv")
ENV = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def uv(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([UV, *args], cwd=cwd, env=ENV, capture_output=True,
                          text=True, encoding="utf-8", timeout=90)


def lock_mode(project: Path) -> str:
    text = (project / "uv.lock").read_text(encoding="utf-8")
    return "prerelease-mode = \"allow\"" if 'prerelease-mode = "allow"' in text else "no prerelease-mode line"


def ascii_only(line: str) -> str:
    """uv draws its error tree with Unicode glyphs; spell them in ASCII for any console."""
    line = line.replace("×", "x").replace("╰─▶", "->").replace("│", "|")
    return line.encode("ascii", "replace").decode("ascii").rstrip()


def show(result: subprocess.CompletedProcess, lines: int = 12) -> None:
    out = [ascii_only(line) for line in result.stderr.splitlines() if line.strip()]
    print(f"exit {result.returncode}")
    for line in out[:lines]:
        print(f"  {line}")


def main() -> None:
    if not UV:
        print("uv is not on PATH; nothing to show")
        return
    python = sys.executable
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "requirements.in").write_text(PIN + "\n", encoding="utf-8")

        print(f"== 1. resolve {PIN} without the flag ==")
        first = uv("pip", "compile", "--offline", "--no-config", "--python", python, "requirements.in", cwd=tmp)
        show(first)
        if "pre-release" not in first.stderr:
            print("the cache does not hold the pinned release; run `uv sync` in the series root first")
            return

        print("\n== 2. the same, with --prerelease=allow ==")
        show(uv("pip", "compile", "--offline", "--no-config", "--python", python,
                "--prerelease=allow", "requirements.in", cwd=tmp), lines=1)

        project = tmp / "mcp-system-info"
        project.mkdir()
        uv("init", "--bare", "--no-workspace", "--python", python, cwd=project)
        print("\n== 3. uv add --prerelease=allow, then look at uv.lock ==")
        show(uv("add", "--offline", "--no-sync", "--prerelease=allow", PIN, cwd=project), lines=1)
        print(f"uv.lock: {lock_mode(project)}")

        print("\n== 4. the next plain uv lock ==")
        show(uv("lock", "--offline", cwd=project), lines=2)
        print(f"uv.lock: {lock_mode(project)}")

        print("\n== 5. delete uv.lock and lock again, still without the setting ==")
        (project / "uv.lock").unlink()
        fresh = uv("lock", "--offline", cwd=project)
        show(fresh, lines=2)
        print(f"its hint says pre-releases weren't enabled: {'pre-releases weren' in fresh.stderr}")

        print("\n== 6. write the mode into pyproject.toml, then lock from scratch ==")
        with (project / "pyproject.toml").open("a", encoding="utf-8") as handle:
            handle.write('\n[tool.uv]\nprerelease = "allow"\n')
        show(uv("lock", "--offline", cwd=project), lines=1)
        print(f"uv.lock: {lock_mode(project)}")
        show(uv("lock", "--offline", cwd=project), lines=1)
        print(f"uv.lock after one more plain uv lock: {lock_mode(project)}")


if __name__ == "__main__":
    main()
