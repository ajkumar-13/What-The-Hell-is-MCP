"""Entry point.

    python -m k8s_responder                      # stdio, what a desktop host spawns
    python -m k8s_responder --context staging    # pick a kubeconfig context
    python -m k8s_responder --max-replicas 5     # tighten the blast-radius limit
    python -m k8s_responder --http               # Streamable HTTP on 127.0.0.1:8000

`mcp.run()` is synchronous and takes the transport as its first argument; stdio
is the default. Credentials are deliberately not loaded here: the first tool
call connects, on a worker thread, so a missing kubeconfig produces a readable
tool error instead of a server that refuses to start.

There is no `--read-only` flag, because a flag would be the wrong mechanism. The
writing tools exist because `__init__.py` imports `remediate`. Delete that one
line and the capability is gone from the process, which is a stronger guarantee
than any switch parsed at runtime.
"""

from __future__ import annotations

import argparse
import dataclasses
import os

from . import mcp
from .app import log
from .cluster import limits, set_limits


def main() -> None:
    parser = argparse.ArgumentParser(prog="k8s-responder")
    parser.add_argument(
        "--context",
        default="",
        help="kubeconfig context to use; ignored when running in-cluster",
    )
    parser.add_argument(
        "--max-replicas",
        type=int,
        default=0,
        help="override the maximum replica count scale_deployment will accept",
    )
    parser.add_argument(
        "--protect",
        default="",
        help="comma-separated namespaces the writing tools must refuse",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve over Streamable HTTP instead of stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.context:
        os.environ["K8S_RESPONDER_CONTEXT"] = args.context

    overrides: dict[str, object] = {}
    if args.max_replicas > 0:
        overrides["max_replicas"] = args.max_replicas
    if args.protect:
        overrides["protected_namespaces"] = frozenset(
            n.strip() for n in args.protect.split(",") if n.strip()
        )
    if overrides:
        set_limits(dataclasses.replace(limits(), **overrides))

    cfg = limits()
    log.info(
        "limits: max_replicas=%s protected=%s max_tail_lines=%s max_log_bytes=%s",
        cfg.max_replicas,
        ",".join(sorted(cfg.protected_namespaces)) or "none",
        cfg.max_tail_lines,
        cfg.max_log_bytes,
    )

    if args.http:
        # Loopback by default. This server can delete pods; a listener on
        # 0.0.0.0 hands that ability to anything on the network.
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
