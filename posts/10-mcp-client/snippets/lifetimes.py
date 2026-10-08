"""Post 10, sections 4 and 11: lifetimes, and the subprocess the cleanup code leaked.

Run from the series root:
    uv run python posts/10-mcp-client/snippets/lifetimes.py

Starts the server under code/05-first-server/ as a stdio subprocess four
times. First with the hand-rolled pattern much 1.x-era client code uses
(enter the transport and the session by hand in one task, exit them in
close() from another), and checks the process table afterwards. Then the
2.x Client alone, entered and exited the same wrong way. Then open_connection
from code/10-mcp-client/src/mcp_host/connection.py with a body that raises,
to show the nesting of the exception that escapes and that the subprocess is
gone. Last, a datetime.timedelta where the SDK wants a float. Kills anything
it leaked before it exits.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import timedelta
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))

import psutil  # noqa: E402
from mcp import Client, ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from mcp_host.connection import ServerSpec, open_connection  # noqa: E402
from system_info import mcp  # noqa: E402

logging.disable(logging.CRITICAL)  # asyncio reports the abandoned exit task on stderr; the output below says it
QUIET = {"PYTHONDONTWRITEBYTECODE": "1"}
params = StdioServerParameters(command=sys.executable, args=["-m", "system_info"], cwd=str(SRC), env=QUIET)


def started() -> list[psutil.Process]:
    # A process can exit while the tree is being walked; walk again if so.
    for _ in range(5):
        try:
            return psutil.Process().children(recursive=True)
        except psutil.Error:
            continue
    return []


def alive(p: psutil.Process) -> bool:
    try:
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def running(processes: list[psutil.Process]) -> int:
    return sum(1 for p in processes if alive(p))


def reap(processes: list[psutil.Process]) -> None:
    for p in processes:
        try:
            p.kill()
        except psutil.Error:
            pass


def flatten(exc: BaseException) -> list[BaseException]:
    nested = getattr(exc, "exceptions", None)
    if nested is None:
        return [exc]
    return [inner for e in nested for inner in flatten(e)]


def depth(exc: BaseException) -> int:
    nested = getattr(exc, "exceptions", None)
    return 0 if nested is None else 1 + max(depth(e) for e in nested)


class HandRolled:
    """The pattern section 4 warns against, spelled exactly as 1.x code spells it."""

    async def open(self):
        # Do not do this.
        self._transport_cm = stdio_client(params)
        self._read, self._write = await self._transport_cm.__aenter__()
        self.session = ClientSession(self._read, self._write)
        await self.session.__aenter__()

    async def close(self):
        await self.session.__aexit__(None, None, None)
        await self._transport_cm.__aexit__(None, None, None)


async def hand_rolled() -> None:
    print("== hand-rolled: entered in one task, closed from another ==")
    conn = HandRolled()
    await asyncio.create_task(conn.open())
    children = started()
    print(f"server process running after open(): {running(children) > 0}")
    try:
        await conn.close()
    except BaseException as exc:  # noqa: BLE001 - the point is to see what arrives
        print(f"close() raised {type(exc).__name__}: {exc}")
    await asyncio.sleep(1.0)
    print(f"server process still running after close(): {running(children) > 0}")
    reap(children)


async def client_alone() -> None:
    print("\n== the 2.x Client, entered in one task, exited from another ==")
    client = Client(params)
    await asyncio.create_task(client.__aenter__())
    children = started()
    try:
        await client.__aexit__(None, None, None)
    except BaseException as exc:  # noqa: BLE001
        print(f"__aexit__ raised {type(exc).__name__}: {exc}")
    await asyncio.sleep(1.0)
    print(f"server process still running after __aexit__: {running(children) > 0}")
    reap(children)


async def exit_stack() -> None:
    print("\n== open_connection, with a body that raises ==")
    spec = ServerSpec.stdio("system-info", sys.executable, "-m", "system_info", cwd=str(SRC), env=QUIET)
    children: list[psutil.Process] = []
    try:
        async with open_connection(spec) as conn:
            await conn.list_tools()
            children = started()
            raise ZeroDivisionError("boom")
    except ZeroDivisionError:
        print("except ZeroDivisionError fired")
    except BaseException as exc:  # noqa: BLE001
        leaves = flatten(exc)
        print(f"escaped as {type(exc).__name__}, nested {depth(exc)} deep; "
              f"members: {[type(e).__name__ for e in leaves]}")
    await asyncio.sleep(1.0)
    print(f"server process running inside the block: {len(children) > 0}; after it: {running(children) > 0}")
    reap(children)


async def timedelta_timeout() -> None:
    print("\n== read_timeout_seconds as a timedelta ==")
    try:
        async with Client(mcp, read_timeout_seconds=timedelta(seconds=5)) as client:
            await client.call_tool("get_system_info", {})
    except BaseException as exc:  # noqa: BLE001
        inner = flatten(exc)[0]
        print(f"escaped as {type(exc).__name__}; inside it: {type(inner).__name__}: {inner}")


async def main() -> None:
    await hand_rolled()
    await client_alone()
    await exit_stack()
    await timedelta_timeout()
    reap(started())


asyncio.run(main())
