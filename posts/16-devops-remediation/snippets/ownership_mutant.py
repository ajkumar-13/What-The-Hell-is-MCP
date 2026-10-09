"""Post 16, section 7: the project's suite with the ownership filter taken out.

Run from the series root:
    uv run python posts/16-devops-remediation/snippets/ownership_mutant.py

Runs the test suite of code/15-devops-responder twice in this process with
pytest, which the root environment provides: once as published, and once with
`remediate.owned_replica_sets` replaced by a pass-through that keeps every
ReplicaSet the label selector returned, which is what filtering by label alone
amounts to. Prints the summary of each run and, for the second, the failing
tests and the assertion that names the wrong image. No file under code/ is
changed, and no cache or bytecode is written there.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

import pytest  # noqa: E402


class LabelOnly:
    """A pytest plugin that swaps the ownership filter for a pass-through."""

    def pytest_sessionstart(self, session):
        from k8s_responder import remediate

        remediate.owned_replica_sets = lambda replica_sets, deployment: list(replica_sets)


def run(label: str, plugins: list) -> str:
    out = io.StringIO()
    args = [
        str(PROJECT / "tests"),
        "--rootdir", str(PROJECT),
        "-c", str(PROJECT / "pyproject.toml"),
        "-p", "no:cacheprovider",
        "-q", "--no-header", "-rf", "--tb=long",
    ]
    with contextlib.redirect_stdout(out):
        code = pytest.main(args, plugins=plugins)
    text = out.getvalue()
    summary = [line for line in text.splitlines() if re.search(r"\d+ (passed|failed)", line)]
    print(f"== {label}: exit code {int(code)} ==")
    print(re.sub(r" in [\d.]+s", "", summary[-1].strip("= ")))
    return text


def main() -> None:
    sys.path[:0] = [str(PROJECT / "src")]
    run("the suite as published", [])
    for name in [m for m in sys.modules if m == "conftest" or m.startswith(("k8s_responder", "test_"))]:
        del sys.modules[name]  # a fresh import for the second session
    text = run("the suite with owned_replica_sets as a pass-through", [LabelOnly()])
    for line in text.splitlines():
        if line.startswith("FAILED"):
            test = line.split(" - ")[0].split("::", 1)[1]
            print(f"FAILED tests/test_server.py::{test}")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if 'assert data["restored_images"]' in line:
            shown = [line] + [x for x in lines[i + 1:i + 6] if x.startswith("E") and x[1:].strip()]
            print()
            print("\n".join(shown))
            break


if __name__ == "__main__":
    main()
