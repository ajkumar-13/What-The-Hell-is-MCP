# Blog 7: DevOps First Responder – Part 1
## Read & Diagnose Your Kubernetes Cluster with AI


> *"It's 3 AM. Your K8s cluster is failing. Instead of typing kubectl commands half-asleep, you ask: 'What's wrong with my cluster?' and get an actual answer."*

---

## Introduction

In Blogs 5-6, we gave AI access to a database, with strict guardrails. Now we're shifting domains entirely: from data analysis to **infrastructure operations**.

We're building an MCP server that connects to a live Kubernetes cluster. No more guessing at `kubectl` commands. You'll ask Claude, "Why is checkout-service crashing?" and it will pull pod statuses, read logs, check events, and give you a diagnosis.

This blog covers the **read-only** tools: listing, inspecting, and diagnosing. Blog 8 adds the ability to *fix* things, with human approval, of course.

### What We're Building

An MCP server that exposes four tools:

| Tool | Purpose |
|------|---------|
| `list_pods` | List pods with status, restarts, age |
| `get_pod_logs` | Read logs from any pod/container |
| `describe_pod` | Full pod details, conditions, events |
| `list_events` | Recent cluster events (warnings, errors) |

Plus a **diagnostic prompt** that pre-loads crashing pod info for instant root-cause analysis.

---

## Prerequisites

| Requirement | How to Get It |
|-------------|---------------|
| Python 3.10+ | python.org |
| A Kubernetes cluster | minikube, kind, Docker Desktop K8s, or a cloud cluster |
| `kubectl` configured | `kubectl cluster-info` should work |
| Blogs 1-4 completed | Understanding of MCP servers, tools, resources, prompts |

### Quick Cluster Setup (If You Don't Have One)

```bash
# Option 1: minikube (recommended for learning)
# Install: https://minikube.sigs.k8s.io/docs/start/
minikube start

# Option 2: kind (Kubernetes IN Docker)
# Install: https://kind.sigs.k8s.io/docs/user/quick-start/
kind create cluster

# Option 3: Docker Desktop
# Enable Kubernetes in Docker Desktop Settings → Kubernetes → Enable

# Verify it works:
kubectl cluster-info
kubectl get pods -A
```

---

## 1. Project Architecture

```
mcp-k8s-agent/
├── pyproject.toml
└── src/
    ├── __init__.py
    ├── server.py          # MCP server: tools, resources, prompts
    ├── k8s_client.py      # Kubernetes API wrapper
    └── formatters.py      # Output formatting for LLM consumption
```

| File | Responsibility |
|------|----------------|
| `server.py` | MCP entry point, tool/resource/prompt definitions |
| `k8s_client.py` | Kubernetes Python client wrapper with async support |
| `formatters.py` | Convert K8s API objects to clean, LLM-readable output |

---

## 2. Project Setup

```bash
mkdir mcp-k8s-agent
cd mcp-k8s-agent
uv init

# kubernetes: Official K8s Python client
# mcp[cli]: MCP server framework + CLI tools
uv add "mcp[cli]" kubernetes
```

Create the `src/__init__.py`:

```python
# src/__init__.py - empty, marks directory as Python package
```

---

## 3. The Kubernetes Client Wrapper

The official `kubernetes` Python client is synchronous. Since MCP servers are async, we wrap the sync calls to avoid blocking the event loop.

Create `src/k8s_client.py`:

```python
# src/k8s_client.py - Kubernetes API Wrapper
import asyncio
import logging
import sys
from datetime import datetime, timezone
from functools import partial

from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Safe logging (never print to stdout)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class K8sClient:
    """Wraps the Kubernetes Python client with async support."""

    def __init__(self):
        self.core_v1: client.CoreV1Api | None = None
        self.apps_v1: client.AppsV1Api | None = None

    def connect(self):
        """
        Load kubeconfig and initialize API clients.
        Tries in-cluster config first (for running inside K8s),
        then falls back to local kubeconfig (~/.kube/config).
        """
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except config.ConfigException:
            config.load_kube_config()
            logger.info("Loaded local kubeconfig")

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        logger.info("Kubernetes API clients initialized")

    async def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous K8s API call in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    # ========== POD OPERATIONS ==========

    async def list_pods(self, namespace: str = "default") -> list[dict]:
        """List pods in a namespace with key status fields."""
        try:
            result = await self._run_sync(
                self.core_v1.list_namespaced_pod, namespace=namespace
            )
        except ApiException as e:
            raise RuntimeError(f"K8s API error listing pods: {e.status} {e.reason}")

        pods = []
        now = datetime.now(timezone.utc)

        for pod in result.items:
            # Calculate age
            created = pod.metadata.creation_timestamp
            age = now - created if created else None
            age_str = _format_duration(age) if age else "unknown"

            # Calculate restarts and ready count
            restarts = 0
            ready_count = 0
            total_containers = len(pod.spec.containers)

            if pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    restarts += cs.restart_count
                    if cs.ready:
                        ready_count += 1

            pods.append(
                {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "status": pod.status.phase,
                    "ready": f"{ready_count}/{total_containers}",
                    "restarts": restarts,
                    "age": age_str,
                    "node": pod.spec.node_name or "unscheduled",
                }
            )

        return pods

    async def get_pod_logs(
        self,
        pod_name: str,
        namespace: str = "default",
        container: str | None = None,
        tail_lines: int = 100,
        previous: bool = False,
    ) -> str:
        """Get logs from a pod (optionally a specific container)."""
        kwargs = {
            "name": pod_name,
            "namespace": namespace,
            "tail_lines": tail_lines,
            "previous": previous,
        }
        if container:
            kwargs["container"] = container

        try:
            logs = await self._run_sync(
                self.core_v1.read_namespaced_pod_log, **kwargs
            )
            return logs or "(no logs available)"
        except ApiException as e:
            if e.status == 404:
                return f"Pod '{pod_name}' not found in namespace '{namespace}'."
            if e.status == 400 and "previous terminated" in str(e.body).lower():
                return "(no previous container logs available)"
            raise RuntimeError(f"K8s API error reading logs: {e.status} {e.reason}")

    async def describe_pod(self, pod_name: str, namespace: str = "default") -> dict:
        """Get detailed pod information including conditions and events."""
        try:
            pod = await self._run_sync(
                self.core_v1.read_namespaced_pod,
                name=pod_name,
                namespace=namespace,
            )
        except ApiException as e:
            if e.status == 404:
                raise RuntimeError(
                    f"Pod '{pod_name}' not found in namespace '{namespace}'."
                )
            raise RuntimeError(f"K8s API error: {e.status} {e.reason}")

        # Get events for this pod
        field_selector = f"involvedObject.name={pod_name}"
        try:
            events = await self._run_sync(
                self.core_v1.list_namespaced_event,
                namespace=namespace,
                field_selector=field_selector,
            )
            event_list = [
                {
                    "type": ev.type,
                    "reason": ev.reason,
                    "message": ev.message,
                    "count": ev.count,
                    "last_seen": ev.last_timestamp.isoformat()
                    if ev.last_timestamp
                    else "unknown",
                }
                for ev in events.items
            ]
        except ApiException:
            event_list = []

        # Build container statuses
        container_statuses = []
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                status_detail = {}
                if cs.state.running:
                    status_detail = {
                        "state": "running",
                        "started_at": cs.state.running.started_at.isoformat()
                        if cs.state.running.started_at
                        else None,
                    }
                elif cs.state.waiting:
                    status_detail = {
                        "state": "waiting",
                        "reason": cs.state.waiting.reason,
                        "message": cs.state.waiting.message,
                    }
                elif cs.state.terminated:
                    status_detail = {
                        "state": "terminated",
                        "reason": cs.state.terminated.reason,
                        "exit_code": cs.state.terminated.exit_code,
                        "message": cs.state.terminated.message,
                    }

                container_statuses.append(
                    {
                        "name": cs.name,
                        "image": cs.image,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count,
                        **status_detail,
                    }
                )

        # Build conditions
        conditions = []
        if pod.status.conditions:
            for cond in pod.status.conditions:
                conditions.append(
                    {
                        "type": cond.type,
                        "status": cond.status,
                        "reason": cond.reason,
                        "message": cond.message,
                    }
                )

        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": pod.status.phase,
            "node": pod.spec.node_name,
            "labels": dict(pod.metadata.labels or {}),
            "containers": container_statuses,
            "conditions": conditions,
            "events": event_list,
        }

    # ========== EVENT OPERATIONS ==========

    async def list_events(
        self,
        namespace: str = "default",
        event_type: str | None = None,
        limit: int = 30,
    ) -> list[dict]:
        """List recent cluster events, optionally filtered by type."""
        try:
            events = await self._run_sync(
                self.core_v1.list_namespaced_event, namespace=namespace
            )
        except ApiException as e:
            raise RuntimeError(f"K8s API error listing events: {e.status} {e.reason}")

        event_list = []
        for ev in events.items:
            if event_type and ev.type != event_type:
                continue
            event_list.append(
                {
                    "type": ev.type,
                    "reason": ev.reason,
                    "object": f"{ev.involved_object.kind}/{ev.involved_object.name}",
                    "message": ev.message,
                    "count": ev.count,
                    "first_seen": ev.first_timestamp.isoformat()
                    if ev.first_timestamp
                    else "unknown",
                    "last_seen": ev.last_timestamp.isoformat()
                    if ev.last_timestamp
                    else "unknown",
                }
            )

        # Sort by last_seen descending, limit results
        event_list.sort(key=lambda e: e["last_seen"], reverse=True)
        return event_list[:limit]

    # ========== DEPLOYMENT OPERATIONS ==========

    async def list_deployments(self, namespace: str = "default") -> list[dict]:
        """List deployments with replica status."""
        try:
            result = await self._run_sync(
                self.apps_v1.list_namespaced_deployment, namespace=namespace
            )
        except ApiException as e:
            raise RuntimeError(
                f"K8s API error listing deployments: {e.status} {e.reason}"
            )

        deployments = []
        for dep in result.items:
            deployments.append(
                {
                    "name": dep.metadata.name,
                    "namespace": dep.metadata.namespace,
                    "replicas": dep.spec.replicas,
                    "ready_replicas": dep.status.ready_replicas or 0,
                    "available_replicas": dep.status.available_replicas or 0,
                    "updated_replicas": dep.status.updated_replicas or 0,
                    "image": dep.spec.template.spec.containers[0].image
                    if dep.spec.template.spec.containers
                    else "unknown",
                }
            )

        return deployments


# ========== HELPERS ==========


def _format_duration(delta) -> str:
    """Format a timedelta into a human-readable age string."""
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    if total_seconds < 86400:
        return f"{total_seconds // 3600}h"
    return f"{total_seconds // 86400}d"


# Global instance
k8s = K8sClient()
```

### Key Design Decisions

| Decision | Reason |
|----------|--------|
| Sync client in thread pool | K8s Python client is sync; `run_in_executor` prevents blocking the event loop |
| In-cluster config fallback | Server can run inside K8s or on your laptop |
| Structured dict output | JSON is easier for LLMs to parse than raw text |
| Age calculation | Humans (and LLMs) understand "3d" better than ISO timestamps |
| Error wrapping | Clean error messages instead of raw API exceptions |

---

## 4. Output Formatters

LLMs work better with clean, structured text. Raw K8s API responses are massive JSON blobs. Our formatters extract what matters.

Create `src/formatters.py`:

```python
# src/formatters.py - Format K8s data for LLM consumption
import json


def format_pod_list(pods: list[dict]) -> str:
    """Format pod list as a readable table."""
    if not pods:
        return "No pods found in this namespace."

    lines = ["| Name | Status | Ready | Restarts | Age | Node |"]
    lines.append("|------|--------|-------|----------|-----|------|")

    for pod in pods:
        lines.append(
            f"| {pod['name']} | {pod['status']} | {pod['ready']} "
            f"| {pod['restarts']} | {pod['age']} | {pod['node']} |"
        )

    # Add summary
    total = len(pods)
    running = sum(1 for p in pods if p["status"] == "Running")
    crashing = sum(1 for p in pods if p["restarts"] > 3)

    summary = f"\n**Summary:** {total} pods | {running} running"
    if crashing:
        summary += f" | {crashing} with high restarts"

    return "\n".join(lines) + summary


def format_pod_detail(detail: dict) -> str:
    """Format detailed pod info for diagnosis."""
    lines = [f"# Pod: {detail['name']}"]
    lines.append(f"**Namespace:** {detail['namespace']}")
    lines.append(f"**Phase:** {detail['phase']}")
    lines.append(f"**Node:** {detail['node']}")

    if detail.get("labels"):
        label_str = ", ".join(f"{k}={v}" for k, v in detail["labels"].items())
        lines.append(f"**Labels:** {label_str}")

    # Containers
    lines.append("\n## Containers\n")
    for c in detail.get("containers", []):
        state = c.get("state", "unknown")
        lines.append(f"### {c['name']} ({state})")
        lines.append(f"- **Image:** {c['image']}")
        lines.append(f"- **Ready:** {c['ready']}")
        lines.append(f"- **Restarts:** {c['restart_count']}")
        if c.get("reason"):
            lines.append(f"- **Reason:** {c['reason']}")
        if c.get("message"):
            lines.append(f"- **Message:** {c['message']}")
        if c.get("exit_code") is not None:
            lines.append(f"- **Exit Code:** {c['exit_code']}")

    # Conditions
    if detail.get("conditions"):
        lines.append("\n## Conditions\n")
        lines.append("| Type | Status | Reason | Message |")
        lines.append("|------|--------|--------|---------|")
        for cond in detail["conditions"]:
            lines.append(
                f"| {cond['type']} | {cond['status']} "
                f"| {cond.get('reason', '-')} | {cond.get('message', '-')} |"
            )

    # Events
    if detail.get("events"):
        lines.append("\n## Recent Events\n")
        lines.append("| Type | Reason | Message | Count |")
        lines.append("|------|--------|---------|-------|")
        for ev in detail["events"][-10:]:  # Last 10 events
            lines.append(
                f"| {ev['type']} | {ev['reason']} "
                f"| {ev.get('message', '-')[:80]} | {ev.get('count', 1)} |"
            )

    return "\n".join(lines)


def format_event_list(events: list[dict]) -> str:
    """Format cluster events for review."""
    if not events:
        return "No events found."

    lines = ["| Type | Reason | Object | Message | Count | Last Seen |"]
    lines.append("|------|--------|--------|---------|-------|-----------|")

    for ev in events:
        msg = (ev.get("message") or "-")[:60]
        lines.append(
            f"| {ev['type']} | {ev['reason']} | {ev['object']} "
            f"| {msg} | {ev.get('count', 1)} | {ev['last_seen']} |"
        )

    warnings = sum(1 for e in events if e["type"] == "Warning")
    if warnings:
        lines.append(f"\n**{warnings} warning events** in this list.")

    return "\n".join(lines)


def format_deployment_list(deployments: list[dict]) -> str:
    """Format deployment list as a readable table."""
    if not deployments:
        return "No deployments found in this namespace."

    lines = ["| Name | Ready | Up-to-date | Available | Image |"]
    lines.append("|------|-------|------------|-----------|-------|")

    for dep in deployments:
        lines.append(
            f"| {dep['name']} | {dep['ready_replicas']}/{dep['replicas']} "
            f"| {dep['updated_replicas']} | {dep['available_replicas']} "
            f"| {dep['image']} |"
        )

    return "\n".join(lines)
```

---

## 5. The MCP Server

Now the main event. We wire up the K8s client and formatters to MCP tools, resources, and prompts.

Create `src/server.py`:

```python
# src/server.py - MCP DevOps First Responder (Read & Diagnose)
import json
import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .k8s_client import k8s
from .formatters import (
    format_pod_list,
    format_pod_detail,
    format_event_list,
    format_deployment_list,
)

# Safe logging (never print to stdout — STDIO transport)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ============ LIFECYCLE ============
@asynccontextmanager
async def lifespan(server: FastMCP):
    """Connect to Kubernetes on startup."""
    k8s.connect()
    logger.info("K8s DevOps First Responder ready")
    try:
        yield
    finally:
        logger.info("K8s DevOps First Responder shutting down")


mcp = FastMCP("K8s DevOps First Responder", lifespan=lifespan)


# ============ TOOLS ============

@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
    }
)
async def list_pods(namespace: str = "default") -> str:
    """
    List all pods in a Kubernetes namespace with their status.

    Returns a table showing each pod's name, status, ready containers,
    restart count, age, and node. Use this to get an overview of what's
    running and spot unhealthy pods.

    Args:
        namespace: Kubernetes namespace to query (default: "default").
                   Use "kube-system" for system pods.
    """
    try:
        pods = await k8s.list_pods(namespace)
        return format_pod_list(pods)
    except RuntimeError as e:
        return f"Error: {e}"


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
    }
)
async def get_pod_logs(
    pod_name: str,
    namespace: str = "default",
    container: str | None = None,
    tail_lines: int = 100,
    previous: bool = False,
) -> str:
    """
    Get logs from a Kubernetes pod.

    Args:
        pod_name: Name of the pod (e.g., "checkout-service-7d8f9").
        namespace: Kubernetes namespace (default: "default").
        container: Specific container name (required for multi-container pods).
        tail_lines: Number of log lines to return (default: 100, max: 500).
        previous: If True, get logs from the previous (crashed) container instance.
                  Useful for diagnosing CrashLoopBackOff.
    """
    tail_lines = max(1, min(tail_lines, 500))  # Cap to prevent context flooding

    try:
        logs = await k8s.get_pod_logs(
            pod_name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines,
            previous=previous,
        )
        header = f"**Logs for {pod_name}"
        if container:
            header += f" (container: {container})"
        if previous:
            header += " [PREVIOUS INSTANCE]"
        header += f" (last {tail_lines} lines):**\n\n"

        return header + f"```\n{logs}\n```"
    except RuntimeError as e:
        return f"Error: {e}"


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
    }
)
async def describe_pod(pod_name: str, namespace: str = "default") -> str:
    """
    Get detailed information about a specific pod, including container
    statuses, conditions, and recent events.

    Use this when a pod is unhealthy and you need to understand why.
    It shows waiting reasons, exit codes, and Kubernetes events.

    Args:
        pod_name: Name of the pod to inspect.
        namespace: Kubernetes namespace (default: "default").
    """
    try:
        detail = await k8s.describe_pod(pod_name, namespace)
        return format_pod_detail(detail)
    except RuntimeError as e:
        return f"Error: {e}"


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
    }
)
async def list_events(
    namespace: str = "default",
    event_type: str | None = None,
    limit: int = 30,
) -> str:
    """
    List recent Kubernetes events in a namespace.

    Events reveal what Kubernetes is doing behind the scenes: scheduling,
    pulling images, restarting containers, scaling, etc. Warning events
    often indicate problems.

    Args:
        namespace: Kubernetes namespace (default: "default").
        event_type: Filter by event type: "Warning" or "Normal".
                    Omit to see all events.
        limit: Maximum number of events to return (default: 30).
    """
    limit = max(1, min(limit, 100))

    try:
        events = await k8s.list_events(
            namespace=namespace, event_type=event_type, limit=limit
        )
        return format_event_list(events)
    except RuntimeError as e:
        return f"Error: {e}"


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
    }
)
async def list_deployments(namespace: str = "default") -> str:
    """
    List all deployments in a namespace with their replica status.

    Shows desired, ready, up-to-date, and available replica counts,
    plus the container image. Useful for understanding the overall
    health and scale of your services.

    Args:
        namespace: Kubernetes namespace (default: "default").
    """
    try:
        deployments = await k8s.list_deployments(namespace)
        return format_deployment_list(deployments)
    except RuntimeError as e:
        return f"Error: {e}"


# ============ RESOURCE ============

@mcp.resource("k8s://cluster-overview")
async def cluster_overview() -> str:
    """
    A snapshot of the cluster state: pods and deployments in the
    default namespace. Attach this to your conversation for context.
    """
    try:
        pods = await k8s.list_pods("default")
        deployments = await k8s.list_deployments("default")

        output = "# Cluster Overview (default namespace)\n\n"
        output += "## Pods\n\n" + format_pod_list(pods)
        output += "\n\n## Deployments\n\n" + format_deployment_list(deployments)
        return output
    except RuntimeError as e:
        return f"Error loading cluster overview: {e}"


# ============ PROMPT ============

@mcp.prompt(title="Diagnose Crash Loop")
async def diagnose_crashloop(namespace: str = "default") -> str:
    """
    Pre-built diagnostic prompt that identifies crashing pods,
    fetches their logs and events, and asks the LLM to diagnose
    the root cause.
    """
    try:
        pods = await k8s.list_pods(namespace)
    except RuntimeError as e:
        return f"Could not list pods: {e}"

    # Find pods in trouble
    crashing = [p for p in pods if p["restarts"] > 3 or p["status"] != "Running"]

    if not crashing:
        return (
            f"All pods in namespace '{namespace}' appear healthy. "
            "No crash loops detected."
        )

    # Build diagnostic context
    context = f"# Crash Loop Diagnosis — namespace: {namespace}\n\n"
    context += f"**{len(crashing)} unhealthy pod(s) detected.**\n\n"

    for pod in crashing[:5]:  # Limit to 5 pods to avoid context overflow
        context += f"---\n## Pod: {pod['name']}\n"
        context += f"- **Status:** {pod['status']}\n"
        context += f"- **Restarts:** {pod['restarts']}\n"
        context += f"- **Ready:** {pod['ready']}\n\n"

        # Try to get logs
        try:
            logs = await k8s.get_pod_logs(
                pod["name"], namespace, tail_lines=30, previous=True
            )
            context += f"### Previous Container Logs (last 30 lines)\n```\n{logs}\n```\n\n"
        except RuntimeError:
            context += "*(Could not retrieve previous logs)*\n\n"

        # Get current logs too
        try:
            current_logs = await k8s.get_pod_logs(
                pod["name"], namespace, tail_lines=30
            )
            context += f"### Current Logs (last 30 lines)\n```\n{current_logs}\n```\n\n"
        except RuntimeError:
            pass

    context += (
        "---\n\n"
        "Please analyze the above pod statuses and logs.\n"
        "1. What is the root cause of each crash loop?\n"
        "2. What specific action should be taken to fix it?\n"
        "3. Are there any patterns across multiple pods?"
    )

    return context


# ============ ENTRY POINT ============

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

---

## 6. Understanding the Design

### Why Structured Output?

We return markdown tables and formatted text instead of raw JSON because:

1. **LLMs read markdown well.** Tables, headers, and bullet points help Claude parse the data.
2. **Context efficiency.** A formatted table is smaller than raw K8s API JSON.
3. **Human readability.** When Claude shows you the output, it's already clean.

### Why Tool Annotations?

Every tool is marked `readOnlyHint: True` because this blog only covers read operations. This tells the host (Claude Desktop) that these tools are safe, no approval dialog needed.

In Blog 8, we'll add write tools (`restart_pod`, `scale_deployment`) with `destructiveHint: True`, which triggers host approval.

### Why a Diagnostic Prompt?

The `diagnose_crashloop` prompt does something powerful: it **pre-fetches data** before the conversation starts. When a user selects this prompt, it:

1. Lists all pods
2. Finds ones with high restarts or non-Running status
3. Fetches their logs (current + previous)
4. Packages everything into a structured question

The LLM gets all the context it needs in one shot, instead of making 5-10 tool calls.

---

## 7. Connecting to Claude Desktop

### Option A: Quick Install

```bash
cd mcp-k8s-agent
uv run mcp install src/server.py
```

### Option B: Manual Configuration

Add to your `claude_desktop_config.json`:

**Windows:**
```json
{
  "mcpServers": {
    "k8s-agent": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\YourName\\mcp-k8s-agent",
        "python",
        "-m",
        "src.server"
      ]
    }
  }
}
```

**macOS/Linux:**
```json
{
  "mcpServers": {
    "k8s-agent": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/yourname/mcp-k8s-agent",
        "python",
        "-m",
        "src.server"
      ]
    }
  }
}
```

> **Note:** We use `python -m src.server` (module mode) because we have relative imports (`from .k8s_client import k8s`). Alternatively, restructure as a proper Python package.

Restart Claude Desktop after updating the config.

---

## 8. Testing

### Create a Test Deployment

Let's deploy something intentionally broken so we have pods to diagnose:

```bash
# Deploy a healthy app
kubectl create deployment nginx-healthy --image=nginx:latest

# Deploy a crashing app (bad image name)
kubectl create deployment broken-app --image=nonexistent-image:v1

# Wait a moment for pods to start/fail
sleep 15

# Verify
kubectl get pods
```

You should see `nginx-healthy` running and `broken-app` in `ErrImagePull` or `ImagePullBackOff`.

### Ask Claude

**Test 1: Overview**
> "What pods are running in my cluster?"

Claude will call `list_pods` and show you a clean table.

**Test 2: Diagnosis**
> "Why is broken-app failing?"

Claude will call `describe_pod` and/or `get_pod_logs` and explain the image pull error.

**Test 3: Events**
> "Show me warning events in the default namespace."

Claude will call `list_events(event_type="Warning")` and highlight issues.

**Test 4: Use the Prompt**
Select the "Diagnose Crash Loop" prompt. It will automatically find broken-app, fetch its logs, and ask Claude for a root cause analysis.

### Clean Up

```bash
kubectl delete deployment nginx-healthy broken-app
```

---

## 9. Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| "No module named kubernetes" | Missing dependency | `uv add kubernetes` |
| "Unable to load kubeconfig" | kubectl not configured | Run `kubectl cluster-info` first |
| "Forbidden: pods" | RBAC permissions | Use cluster-admin for local dev |
| Empty pod list | Wrong namespace | Try `namespace="kube-system"` |
| "Server exited immediately" | Import error or crash | Run `uv run python -m src.server` manually |

---

## Key Takeaways
- Kubernetes Python client for real cluster access
- Async wrappers for sync API calls (run_in_executor)
- Structured output (markdown tables) for LLM readability
- Tool annotations mark all tools as read-only
- Diagnostic prompt pre-fetches crash data
- Formatters keep context clean and efficient


---

## What's Next?

Diagnosing is half the battle. Now let's give our agent the power to actually **fix** things—safely.

In **Blog 8: DevOps First Responder – Part 2**, we'll add:
- `restart_pod` — Delete and recreate a crashing pod (with approval)
- `scale_deployment` — Scale replicas up or down
- `rollback_deployment` — Rollback to a previous revision
- Human-in-the-loop for every mutating action

The same approval pattern from Blog 6 (database writes), now applied to infrastructure.

---

## Quick Reference

### Project Structure
```
mcp-k8s-agent/
├── pyproject.toml
└── src/
    ├── __init__.py
    ├── server.py
    ├── k8s_client.py
    └── formatters.py
```

### Tools
| Tool | Arguments | Returns |
|------|-----------|---------|
| `list_pods` | `namespace` | Pod table with status |
| `get_pod_logs` | `pod_name`, `namespace`, `container`, `tail_lines`, `previous` | Log text |
| `describe_pod` | `pod_name`, `namespace` | Detailed pod info |
| `list_events` | `namespace`, `event_type`, `limit` | Event table |
| `list_deployments` | `namespace` | Deployment table |

### Resource
| URI | Description |
|-----|-------------|
| `k8s://cluster-overview` | Pods + deployments in default namespace |

### Prompt
| Name | Description |
|------|-------------|
| `diagnose_crashloop` | Auto-detects crashing pods, fetches logs, requests diagnosis |

---

| [← Blog 6: Database Analyst Part 2](../blog-6/blog.md) | [Blog 8: DevOps First Responder Part 2 →](../blog-8/blog.md) |
|:---|---:|
