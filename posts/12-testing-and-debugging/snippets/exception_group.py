"""Post 12, section 4: an exception that leaves `async with Client(...)` arrives wrapped.

Run from the series root:
    uv run python posts/12-testing-and-debugging/snippets/exception_group.py

Imports the first-server project under code/05-first-server/ and connects an
in-memory client. Inside the block it raises ZeroDivisionError, then reports
which `except` clause caught it, how deep the wrapping goes, and what a
recursive unwrap finds.
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


def flatten(exc: BaseException) -> list[BaseException]:
    """Unwrap nested ExceptionGroups, as code/10-mcp-client/tests/test_client.py does."""
    nested = getattr(exc, "exceptions", None)
    if nested is None:
        return [exc]
    return [inner for e in nested for inner in flatten(e)]


def depth(exc: BaseException) -> int:
    """How many ExceptionGroup layers sit above the first plain exception."""
    nested = getattr(exc, "exceptions", None)
    return 0 if nested is None else 1 + depth(nested[0])


async def main() -> None:
    try:
        async with Client(mcp) as c:
            await c.list_tools()
            raise ZeroDivisionError("boom")
    except ZeroDivisionError:
        print("A: caught as ZeroDivisionError")
    except BaseException as exc:
        print(f"A: caught as {type(exc).__name__}: {exc}")
        print(f"A: .exceptions -> {getattr(exc, 'exceptions', None)}")
        print(f"B: group layers above the error: {depth(exc)}")
        print(f"C: flatten(exc) -> {flatten(exc)!r}")

    try:
        async with Client(mcp) as c:
            await c.list_tools()
            raise ZeroDivisionError("boom")
    except* ZeroDivisionError as group:  # Python 3.11 and later
        print(f"D: except* ZeroDivisionError matched; leaves: {flatten(group)!r}")


asyncio.run(main())
