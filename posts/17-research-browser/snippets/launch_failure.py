"""Post 17, section 11: what a host sees when Chromium cannot be launched.

Run from the series root:
    uv run python posts/17-research-browser/snippets/launch_failure.py

With no argument this is the client. It starts itself again with `--serve` as
a stdio server, a flag that is not meant to be run by hand, writes one
`server/discover` request to it, and prints the exit status, what came back on
standard output, and the last line of standard error.

The server half imports the real package and swaps the default fetcher factory
for a stand-in whose `start()` raises `BrowserUnavailable`, which is what
`PlaywrightFetcher.start()` raises when the browser binary is missing. No
browser is launched, on this machine or on one that has Chromium installed.
The server exits by itself; the client kills it if it has not within 60 seconds.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "17-research-browser"

sys.path[:0] = [str(PROJECT / "src")]

REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "server/discover",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    },
}


def serve() -> None:
    import research_browser
    from research_browser.browser import BrowserUnavailable, browser

    class NoBinaryFetcher:
        """Stands in for PlaywrightFetcher on a machine without the browser binary."""

        name = "no-binary"

        async def start(self) -> None:
            raise BrowserUnavailable(
                "could not launch Chromium (stand-in for PlaywrightFetcher.start(); "
                "the real message names `playwright install chromium`)"
            )

        async def close(self) -> None:
            pass

    browser._factory = lambda: NoBinaryFetcher()
    research_browser.mcp.run()


def main() -> None:
    child = subprocess.Popen(
        [sys.executable, __file__, "--serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        out, err = child.communicate(json.dumps(REQUEST) + "\n", timeout=60)
    except subprocess.TimeoutExpired:
        child.kill()
        out, err = child.communicate()
        print("the server did not exit within 60 seconds and was killed")
    lines = [line.strip(" |") for line in err.splitlines() if line.strip(" |+-")]
    raised = next((line for line in reversed(lines) if "BrowserUnavailable:" in line), "(none)")
    print("one server/discover request to a stdio server whose browser cannot launch")
    print(f"   exit status:     {child.returncode}")
    print(f"   standard output: {out!r}")
    print(f"   standard error ends with the traceback of: {raised}")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        main()
