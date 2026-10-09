"""Post 12, section 4: a yield fixture that hands over a client, and the fix.

Run from the series root:
    uv run python posts/12-testing-and-debugging/snippets/yield_fixture.py

Writes three tiny test files into a temporary directory and runs pytest on
each in a child process, with the first-server project under
code/05-first-server/ on the import path. The first file opens the client in a
yield fixture; the second opens it inside each test body; the third has an
assert that fails inside the client block. It prints pytest's summary for each
run, the full message of the first teardown error and the assertion pytest
reports, then deletes the directory.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"

CONFIG = """\
[pytest]
asyncio_mode = auto
"""

YIELD_FIXTURE = '''\
import pytest
from mcp import Client

from system_info import mcp


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c          # every test in the file now errors


async def test_list_tools(client):
    assert (await client.list_tools()).tools


async def test_call_a_tool(client):
    result = await client.call_tool("get_system_info", {})
    assert result.is_error is False
'''

IN_THE_BODY = '''\
from mcp import Client

from system_info import mcp


async def test_list_tools():
    async with Client(mcp) as c:
        assert (await c.list_tools()).tools


async def test_call_a_tool():
    async with Client(mcp) as c:
        result = await c.call_tool("get_system_info", {})
        assert result.is_error is False
'''


FAILING = '''\
from mcp import Client

from system_info import mcp


async def test_a_tool_that_is_not_there():
    async with Client(mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert "reboot" in names, "reboot is not published"
'''


def run(directory: Path, name: str, source: str) -> str:
    (directory / name).write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", name, "-q", "-rfE", "-p", "no:cacheprovider",
         "--color=no"],
        cwd=directory, env=env, capture_output=True, text=True, timeout=110,
    )
    return proc.stdout


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / "pytest.ini").write_text(CONFIG, encoding="utf-8")

        out = run(directory, "test_yield_fixture.py", YIELD_FIXTURE)
        print("== the client handed over by a yield fixture ==")
        summary = [line for line in out.splitlines() if line.startswith(("ERROR ", "FAILED "))]
        last = out.strip().splitlines()[-1]
        for line in summary:
            print(line)
        print(re.sub(r" in [0-9.]+s", "", last).strip("= "))
        errors = re.findall(r"^E\s+(RuntimeError: .*)$", out, flags=re.M)
        print("first teardown error:", errors[0] if errors else "(none)")

        out = run(directory, "test_in_the_body.py", IN_THE_BODY)
        print("\n== the client opened inside each test body ==")
        last = out.strip().splitlines()[-1]
        print(re.sub(r" in [0-9.]+s", "", last).strip("= "))

        out = run(directory, "test_failing.py", FAILING)
        print("\n== an assert that fails inside the client block ==")
        for line in out.splitlines():
            if line.startswith("FAILED "):
                print(line)
        errors = re.findall(r"^[\s|]*(AssertionError: .*)$", out, flags=re.M)
        print("assertion reported:", errors[0] if errors else "(none)")
        print("traceback printed as an exception group:", "Exception Group Traceback" in out)
        last = out.strip().splitlines()[-1]
        print(re.sub(r" in [0-9.]+s", "", last).strip("= "))


main()
