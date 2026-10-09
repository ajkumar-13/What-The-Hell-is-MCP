"""Post 15, section 6: get_logs, bounded in lines and in bytes, against the fake cluster.

Run from the series root:
    uv run python posts/15-devops-responder/snippets/bounded_logs.py

Installs the fixture of tests/conftest.py as the cluster,
and calls the real get_logs tool over an in-memory client:

1. the 500-line log of the suite's truncation test, asked for 500 lines under
   a 2,000-byte ceiling;
2. absurd bounds, to show the clamping and what reached the fake API;
3. a previous instance that has no log, and a pod that does not exist.

Then it cuts one log that ends in a traceback both ways, oldest end and newest
end, with the project's own `_tail_bytes` for the second.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import logging  # noqa: E402

from conftest import populated  # noqa: E402
from mcp import Client  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import cluster  # noqa: E402
from k8s_responder.inspect import _tail_bytes  # noqa: E402

logging.getLogger().setLevel(logging.WARNING)

SHOWN = ("pod", "lines_requested", "lines_returned", "bytes_returned", "truncated",
         "dropped_bytes", "dropped_lines", "note")


async def main() -> None:
    state = populated()
    # The same log the suite's test_get_logs_caps_bytes_and_says_how_much_it_dropped uses.
    big = "\n".join(f"{i:04d} " + "x" * 60 for i in range(500))
    state.logs[("checkout-3c-aaa", "", False)] = big
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))

    async with Client(k8s_responder.mcp) as client:
        r = await client.call_tool(
            "get_logs", {"pod_name": "checkout-3c-aaa", "tail_lines": 500, "max_bytes": 2000})
        data = r.structured_content
        print(f"1. a log of {big.count(chr(10)) + 1} lines and {len(big.encode())} bytes, "
              "tail_lines=500, max_bytes=2000")
        print(json.dumps({k: data[k] for k in SHOWN}, indent=2))
        kept = data["logs"].splitlines()
        print(f"   first line kept: {kept[0][:12]}...   last line kept: {kept[-1][:12]}...")
        print()

        r = await client.call_tool(
            "get_logs", {"pod_name": "checkout-3c-aaa", "tail_lines": 100_000, "max_bytes": 10**9})
        sent = state.recorder.kwargs_for("read_namespaced_pod_log")[-1]
        cfg = cluster.limits()
        print("2. tail_lines=100000, max_bytes=1000000000")
        print(f"   lines_requested in the result: {r.structured_content['lines_requested']}")
        print(f"   tail_lines the API received:   {sent['tail_lines']}")
        print(f"   configured maxima: max_tail_lines={cfg.max_tail_lines}, "
              f"max_log_bytes={cfg.max_log_bytes}")
        print(f"   bytes_returned: {r.structured_content['bytes_returned']}, "
              f"note: {r.structured_content['note']}")
        print()

        print("3. two reads that find nothing")
        for args in ({"pod_name": "checkout-3c-bbb", "previous": True}, {"pod_name": "ghost"}):
            r = await client.call_tool("get_logs", args)
            d = r.structured_content
            print(f"   {json.dumps(args)}: isError={str(r.is_error).lower()}, "
                  f"logs={d['logs']!r}")
            print(f"     note: {d['note']}")
    cluster.use(None)
    print()

    crash = "\n".join(
        [json.dumps({"level": "info", "seq": i, "msg": "request served"}) for i in range(200)]
        + ["Traceback (most recent call last):", "KeyError: 'DB_PASSWORD'"])
    raw = crash.encode()
    oldest = raw[:2000].decode(errors="replace")
    newest, dropped_bytes, dropped_lines = _tail_bytes(crash, 2000)
    print(f"4. a {len(raw)}-byte log that ends in a traceback, cut to 2000 bytes")
    print(f"   oldest end kept: contains KeyError={'KeyError' in oldest}, "
          f"last line kept={oldest.splitlines()[-1]!r}")
    print(f"   newest end kept: contains KeyError={'KeyError' in newest}, "
          f"first line kept={newest.splitlines()[0]!r}")
    print(f"   _tail_bytes reports dropped_bytes={dropped_bytes}, dropped_lines={dropped_lines}")


if __name__ == "__main__":
    asyncio.run(main())
