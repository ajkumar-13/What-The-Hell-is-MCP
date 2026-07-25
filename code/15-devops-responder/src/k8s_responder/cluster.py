"""Talking to Kubernetes, and the limits on what we are allowed to do.

Three jobs live here.

1. **Connecting.** In-cluster service-account credentials when the server runs
   as a pod, a kubeconfig context otherwise.
2. **Getting off the event loop.** The official `kubernetes` client is
   *synchronous*. Every call is a blocking HTTP request. Calling one directly
   from an `async def` tool stalls the whole transport: no progress
   notifications, no cancellation, no second tool call, until the API server
   answers. `call()` below is the only sanctioned way to reach the API, and it
   hands the blocking work to a worker thread with `asyncio.to_thread`.
3. **Blast-radius policy.** Maximum replica count and the set of namespaces the
   writing tools refuse to touch. Reads are never restricted: you want to be
   able to look at kube-system. Writes are.

A note on `asyncio.to_thread` versus the older
`asyncio.get_event_loop().run_in_executor(None, ...)`: `get_event_loop()` is
deprecated inside a coroutine, returns the wrong thing when no loop is running,
and in a threaded context can silently create a second loop. `to_thread` is the
supported spelling and it forwards keyword arguments, so no `functools.partial`
dance either.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import urllib3.exceptions
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from .app import log

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ClusterError(RuntimeError):
    """Anything the Kubernetes API refused to do, in a shape tools can read.

    `status` carries the HTTP status when there was one, so callers can branch
    on 404 without importing the kubernetes exception type everywhere.
    """

    def __init__(self, message: str, status: int = 0, reason: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class PolicyError(ClusterError):
    """A change this server refuses to make, whatever the cluster would allow.

    This is a local guard, not an RBAC decision. RBAC is the control that
    actually matters; this one exists so an obvious mistake is caught before a
    human is asked to approve it.
    """


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------

_DEFAULT_PROTECTED = (
    "kube-system",
    "kube-public",
    "kube-node-lease",
)


@dataclass(frozen=True)
class Limits:
    """Blast-radius and context-size limits.

    Every one of these is a deliberate refusal to let a model do something at a
    scale nobody asked for. They are read from the environment at import time so
    an operator can tighten them without touching code.
    """

    max_replicas: int = 20
    protected_namespaces: frozenset[str] = field(
        default_factory=lambda: frozenset(_DEFAULT_PROTECTED)
    )
    max_tail_lines: int = 500
    default_tail_lines: int = 100
    max_log_bytes: int = 40_000
    default_log_bytes: int = 20_000
    high_restart_count: int = 3


def _int_env(name: str, fallback: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        log.warning("ignoring %s=%r, not an integer", name, raw)
        return fallback


def _limits_from_env() -> Limits:
    protected = os.environ.get("K8S_RESPONDER_PROTECTED_NAMESPACES")
    if protected is None:
        names = frozenset(_DEFAULT_PROTECTED)
    else:
        names = frozenset(n.strip() for n in protected.split(",") if n.strip())

    return Limits(
        max_replicas=_int_env("K8S_RESPONDER_MAX_REPLICAS", 20),
        protected_namespaces=names,
        max_tail_lines=_int_env("K8S_RESPONDER_MAX_TAIL_LINES", 500),
        max_log_bytes=_int_env("K8S_RESPONDER_MAX_LOG_BYTES", 40_000),
    )


_limits: Limits = _limits_from_env()


def limits() -> Limits:
    """The limits currently in force."""
    return _limits


def set_limits(new: Limits) -> None:
    """Replace the limits. Used by tests and by `__main__` for CLI overrides."""
    global _limits
    _limits = new


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------


@dataclass
class Connection:
    """The two API groups this server needs, plus where they point.

    Holding the API objects in a value rather than reaching for a module-level
    singleton is what makes the test suite possible: `use()` swaps in a fake
    pair and nothing else in the package knows the difference.
    """

    core_v1: Any
    apps_v1: Any
    context: str = ""
    describe: str = "unknown"


_connection: Connection | None = None
_connect_lock: asyncio.Lock | None = None


def connect(context: str | None = None) -> Connection:
    """Load credentials and build the API clients. Blocking; call via `to_thread`.

    In-cluster configuration is tried first, because when this server runs as a
    pod that is the only correct answer. Falling through to a developer's
    kubeconfig inside a cluster would be both surprising and wrong.
    """
    try:
        config.load_incluster_config()
        where = "in-cluster service account"
        active = ""
    except config.ConfigException:
        try:
            contexts, active_context = config.list_kube_config_contexts()
        except config.ConfigException as exc:
            raise ClusterError(
                "No Kubernetes credentials. This server needs either an "
                "in-cluster service account or a kubeconfig file. "
                f"Underlying error: {exc}"
            ) from exc

        names = [c["name"] for c in (contexts or [])]
        if context:
            if context not in names:
                raise ClusterError(
                    f"Context {context!r} is not in the kubeconfig. "
                    f"Available contexts: {', '.join(names) or 'none'}."
                )
            config.load_kube_config(context=context)
            active = context
        else:
            config.load_kube_config()
            active = (active_context or {}).get("name", "")
        where = f"kubeconfig context {active or 'default'}"

    conn = Connection(
        core_v1=client.CoreV1Api(),
        apps_v1=client.AppsV1Api(),
        context=active,
        describe=where,
    )
    log.info("connected to Kubernetes using %s", where)
    return conn


def use(connection: Connection | None) -> None:
    """Install a connection directly, bypassing credential loading.

    The seam the tests hang the fake cluster off. Passing None resets it.
    """
    global _connection
    _connection = connection


async def current() -> Connection:
    """The active connection, connecting on first use.

    The lock matters: two tools called concurrently would otherwise both decide
    the connection was missing and both load credentials.
    """
    global _connection, _connect_lock
    if _connection is not None:
        return _connection

    if _connect_lock is None:
        _connect_lock = asyncio.Lock()

    async with _connect_lock:
        if _connection is None:
            context = os.environ.get("K8S_RESPONDER_CONTEXT") or None
            _connection = await asyncio.to_thread(connect, context)
        return _connection


async def call(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run one blocking Kubernetes API call on a worker thread.

    This is the single chokepoint. If a call to the cluster does not go through
    here it is running on the event loop, and the transport is frozen for as
    long as the API server takes to answer.
    """
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except ApiException as exc:
        raise ClusterError(
            f"Kubernetes API error {exc.status} {exc.reason}".strip(),
            status=exc.status or 0,
            reason=exc.reason or "",
        ) from exc
    except urllib3.exceptions.HTTPError as exc:
        raise ClusterError(f"Could not reach the Kubernetes API: {exc}") from exc


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


def format_age(created: datetime | None, now: datetime | None = None) -> str:
    """Render an object's age the way kubectl does: 45s, 12m, 3h, 6d."""
    if created is None:
        return "unknown"
    now = now or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)

    seconds = int((now - created).total_seconds())
    if seconds < 0:
        return "0s"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86_400}d"


def timestamp(value: datetime | None) -> str:
    """ISO-8601, or the string 'unknown'. Never None, so the schema stays simple."""
    return value.isoformat() if value else "unknown"


def clamp(value: int, low: int, high: int) -> int:
    """Clamp rather than reject.

    A model that asks for 100000 log lines is not being malicious, it just does
    not know the limit. Silently doing the sensible thing beats an error it has
    to recover from, as long as the result says what was actually done.
    """
    return max(low, min(value, high))
