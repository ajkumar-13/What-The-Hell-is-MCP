# Blog 8: DevOps First Responder – Part 2
## Fix & Remediate with Human-in-the-Loop Approval

*Reading Time: 25 minutes*

---

> *"Diagnosing is half the battle. Now let's give our agent the power to actually fix things—safely."*

---

## Introduction

In Blog 7, we built an MCP server that reads from a Kubernetes cluster: listing pods, fetching logs, describing resources, and diagnosing crash loops.

But reading doesn't fix anything. When it's 3 AM and the checkout service is down, you don't just want *diagnosis*—you want *action*.

Today we're adding **write operations** to our K8s agent:

| Tool | Action | Risk Level |
|------|--------|------------|
| `restart_pod` | Delete a pod so its controller recreates it | Medium |
| `scale_deployment` | Change the number of replicas | Medium |
| `rollback_deployment` | Roll back to a previous revision | High |

Every operation requires **human approval** via MCP tool annotations, exactly like the database write pattern from Blog 6.

---

## 1. The Approval Pattern (Review)

This is the same human-in-the-loop pattern from Blog 6, applied to infrastructure:

```
┌─────────────────────────────────────────────────────────────┐
│              HUMAN-IN-THE-LOOP FOR K8s ACTIONS              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. User: "Restart the payment-service pod"                 │
│                                                             │
│  2. LLM decides: call restart_pod("payment-service-xyz")    │
│                                                             │
│  3. Tool has destructiveHint: true → Host shows dialog:     │
│     ┌─────────────────────────────────────────────────┐    │
│     │ ⚠️  Pod Restart Requested                        │    │
│     │                                                  │    │
│     │  Pod: payment-service-7d8f9b6c4f-x2k9j          │    │
│     │  Namespace: production                           │    │
│     │                                                  │    │
│     │  [Cancel]                    [Allow]             │    │
│     └─────────────────────────────────────────────────┘    │
│                                                             │
│  4. User clicks "Allow" → Pod deleted, controller recreates │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

> ⚠️ **Reminder:** Host approval dialogs are a UX feature, not a security guarantee. Your code must still validate inputs and handle errors gracefully. Some hosts may not show dialogs at all.

---

## 2. Extending the K8s Client

We need three new methods on our K8s client. Add these to `src/k8s_client.py`:

```python
# Add these methods to the K8sClient class in src/k8s_client.py

    # ========== MUTATING OPERATIONS ==========

    async def delete_pod(self, pod_name: str, namespace: str = "default") -> dict:
        """
        Delete a pod. If managed by a Deployment/ReplicaSet, the controller
        will automatically create a replacement.
        
        Returns info about the deleted pod.
        """
        # First, verify the pod exists and get its info
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

        # Check if pod is managed by a controller (safe to delete)
        owner_refs = pod.metadata.owner_references or []
        managed = any(ref.controller for ref in owner_refs)

        # Delete the pod
        try:
            await self._run_sync(
                self.core_v1.delete_namespaced_pod,
                name=pod_name,
                namespace=namespace,
            )
        except ApiException as e:
            raise RuntimeError(f"Failed to delete pod: {e.status} {e.reason}")

        return {
            "pod_name": pod_name,
            "namespace": namespace,
            "managed_by_controller": managed,
            "warning": None if managed else (
                "This pod is NOT managed by a controller. "
                "It will NOT be recreated automatically."
            ),
        }

    async def scale_deployment(
        self,
        deployment_name: str,
        replicas: int,
        namespace: str = "default",
    ) -> dict:
        """Scale a deployment to the specified replica count."""
        # Get current state first
        try:
            dep = await self._run_sync(
                self.apps_v1.read_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
            )
        except ApiException as e:
            if e.status == 404:
                raise RuntimeError(
                    f"Deployment '{deployment_name}' not found "
                    f"in namespace '{namespace}'."
                )
            raise RuntimeError(f"K8s API error: {e.status} {e.reason}")

        old_replicas = dep.spec.replicas

        # Apply the scale
        body = {"spec": {"replicas": replicas}}
        try:
            await self._run_sync(
                self.apps_v1.patch_namespaced_deployment_scale,
                name=deployment_name,
                namespace=namespace,
                body=body,
            )
        except ApiException as e:
            raise RuntimeError(f"Failed to scale deployment: {e.status} {e.reason}")

        return {
            "deployment": deployment_name,
            "namespace": namespace,
            "previous_replicas": old_replicas,
            "new_replicas": replicas,
        }

    async def rollback_deployment(
        self,
        deployment_name: str,
        namespace: str = "default",
        revision: int | None = None,
    ) -> dict:
        """
        Rollback a deployment. If revision is None, rolls back to
        the previous revision.
        """
        # Get current deployment
        try:
            dep = await self._run_sync(
                self.apps_v1.read_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
            )
        except ApiException as e:
            if e.status == 404:
                raise RuntimeError(
                    f"Deployment '{deployment_name}' not found "
                    f"in namespace '{namespace}'."
                )
            raise RuntimeError(f"K8s API error: {e.status} {e.reason}")

        current_image = "unknown"
        if dep.spec.template.spec.containers:
            current_image = dep.spec.template.spec.containers[0].image

        # Get revision history via ReplicaSets
        try:
            rs_list = await self._run_sync(
                self.apps_v1.list_namespaced_replica_set,
                namespace=namespace,
                label_selector=",".join(
                    f"{k}={v}"
                    for k, v in (dep.spec.selector.match_labels or {}).items()
                ),
            )
        except ApiException:
            rs_list = None

        # Perform rollback via patch (remove current template hash to trigger rollback)
        # Kubernetes rollback is done by patching the deployment with a previous
        # ReplicaSet's template. For simplicity, we use the rollback annotation approach.
        annotations = dep.spec.template.metadata.annotations or {}
        
        # The standard way to rollback is to use kubectl rollout undo, which
        # patches the deployment with the previous ReplicaSet's pod template.
        # We'll find the previous RS and patch with its template.
        if rs_list and rs_list.items:
            # Sort ReplicaSets by revision number
            sorted_rs = sorted(
                rs_list.items,
                key=lambda rs: int(
                    (rs.metadata.annotations or {}).get(
                        "deployment.kubernetes.io/revision", "0"
                    )
                ),
                reverse=True,
            )

            # Current is first, previous is second
            if len(sorted_rs) < 2:
                raise RuntimeError(
                    "No previous revision found. Cannot rollback."
                )

            target_rs = sorted_rs[1]  # Previous revision
            if revision:
                # Find specific revision
                for rs in sorted_rs:
                    rev = int(
                        (rs.metadata.annotations or {}).get(
                            "deployment.kubernetes.io/revision", "0"
                        )
                    )
                    if rev == revision:
                        target_rs = rs
                        break
                else:
                    raise RuntimeError(f"Revision {revision} not found.")

            target_image = "unknown"
            if target_rs.spec.template.spec.containers:
                target_image = target_rs.spec.template.spec.containers[0].image

            # Patch deployment with the target RS's pod template
            patch_body = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": c.name,
                                    "image": c.image,
                                }
                                for c in target_rs.spec.template.spec.containers
                            ]
                        }
                    }
                }
            }

            try:
                await self._run_sync(
                    self.apps_v1.patch_namespaced_deployment,
                    name=deployment_name,
                    namespace=namespace,
                    body=patch_body,
                )
            except ApiException as e:
                raise RuntimeError(
                    f"Failed to rollback deployment: {e.status} {e.reason}"
                )

            target_revision = int(
                (target_rs.metadata.annotations or {}).get(
                    "deployment.kubernetes.io/revision", "?"
                )
            )

            return {
                "deployment": deployment_name,
                "namespace": namespace,
                "previous_image": current_image,
                "rolled_back_to_image": target_image,
                "target_revision": target_revision,
            }

        raise RuntimeError("Could not find ReplicaSet history for rollback.")

    async def get_deployment_history(
        self, deployment_name: str, namespace: str = "default"
    ) -> list[dict]:
        """Get revision history for a deployment."""
        try:
            dep = await self._run_sync(
                self.apps_v1.read_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
            )
        except ApiException as e:
            if e.status == 404:
                raise RuntimeError(
                    f"Deployment '{deployment_name}' not found."
                )
            raise RuntimeError(f"K8s API error: {e.status} {e.reason}")

        # Get ReplicaSets for this deployment
        try:
            rs_list = await self._run_sync(
                self.apps_v1.list_namespaced_replica_set,
                namespace=namespace,
                label_selector=",".join(
                    f"{k}={v}"
                    for k, v in (dep.spec.selector.match_labels or {}).items()
                ),
            )
        except ApiException as e:
            raise RuntimeError(f"Failed to list ReplicaSets: {e.status} {e.reason}")

        history = []
        for rs in rs_list.items:
            revision = int(
                (rs.metadata.annotations or {}).get(
                    "deployment.kubernetes.io/revision", "0"
                )
            )
            image = "unknown"
            if rs.spec.template.spec.containers:
                image = rs.spec.template.spec.containers[0].image

            history.append(
                {
                    "revision": revision,
                    "image": image,
                    "replicas": rs.status.replicas or 0,
                    "ready_replicas": rs.status.ready_replicas or 0,
                    "created": rs.metadata.creation_timestamp.isoformat()
                    if rs.metadata.creation_timestamp
                    else "unknown",
                }
            )

        history.sort(key=lambda h: h["revision"], reverse=True)
        return history
```

---

## 3. Adding Format Helpers

Add these formatting functions to `src/formatters.py`:

```python
# Add to src/formatters.py

def format_restart_result(result: dict) -> str:
    """Format pod restart result."""
    lines = [
        f"✅ **Pod Restarted Successfully**\n",
        f"- **Pod:** {result['pod_name']}",
        f"- **Namespace:** {result['namespace']}",
        f"- **Managed by controller:** {'Yes' if result['managed_by_controller'] else 'No'}",
    ]
    if result.get("warning"):
        lines.append(f"\n⚠️ **Warning:** {result['warning']}")
    else:
        lines.append(
            "\nThe controller will automatically create a new pod to replace the deleted one."
        )
    return "\n".join(lines)


def format_scale_result(result: dict) -> str:
    """Format deployment scale result."""
    direction = "up" if result["new_replicas"] > result["previous_replicas"] else "down"
    return (
        f"✅ **Deployment Scaled {direction.title()}**\n\n"
        f"- **Deployment:** {result['deployment']}\n"
        f"- **Namespace:** {result['namespace']}\n"
        f"- **Previous replicas:** {result['previous_replicas']}\n"
        f"- **New replicas:** {result['new_replicas']}\n\n"
        f"Pods will reach the desired count shortly. "
        f"Use `list_pods` to verify."
    )


def format_rollback_result(result: dict) -> str:
    """Format deployment rollback result."""
    return (
        f"✅ **Deployment Rolled Back**\n\n"
        f"- **Deployment:** {result['deployment']}\n"
        f"- **Namespace:** {result['namespace']}\n"
        f"- **Previous image:** `{result['previous_image']}`\n"
        f"- **Rolled back to:** `{result['rolled_back_to_image']}`\n"
        f"- **Target revision:** {result['target_revision']}\n\n"
        f"Kubernetes is now rolling out the previous version. "
        f"Use `list_pods` to monitor progress."
    )


def format_deployment_history(history: list[dict]) -> str:
    """Format deployment revision history."""
    if not history:
        return "No revision history found."

    lines = ["| Revision | Image | Replicas | Ready | Created |"]
    lines.append("|----------|-------|----------|-------|---------|")

    for rev in history:
        lines.append(
            f"| {rev['revision']} | `{rev['image']}` "
            f"| {rev['replicas']} | {rev['ready_replicas']} "
            f"| {rev['created']} |"
        )

    current = history[0] if history else None
    if current:
        lines.append(f"\n**Current revision:** {current['revision']} (`{current['image']}`)")

    return "\n".join(lines)
```

---

## 4. The Write Tools

Now we add the mutating tools to `src/server.py`. Add these after the existing read-only tools:

```python
# Add to src/server.py after the existing read tools

# Import the new formatters at the top:
# from .formatters import (
#     ...,
#     format_restart_result,
#     format_scale_result,
#     format_rollback_result,
#     format_deployment_history,
# )


# ============ MUTATING TOOLS (require approval) ============

@mcp.tool(
    annotations={
        "destructiveHint": True,
        "readOnlyHint": False,
        "idempotentHint": False,
    }
)
async def restart_pod(pod_name: str, namespace: str = "default") -> str:
    """
    Restart a pod by deleting it. If managed by a Deployment or ReplicaSet,
    Kubernetes will automatically create a replacement.

    ⚠️ This is a destructive action. The pod will be terminated and
    any in-memory state will be lost.

    Args:
        pod_name: Exact name of the pod to restart (e.g., "checkout-7d8f9-abc12").
        namespace: Kubernetes namespace (default: "default").
    """
    try:
        result = await k8s.delete_pod(pod_name, namespace)
        return format_restart_result(result)
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(
    annotations={
        "destructiveHint": True,
        "readOnlyHint": False,
        "idempotentHint": True,  # Scaling to same count is safe to retry
    }
)
async def scale_deployment(
    deployment_name: str,
    replicas: int,
    namespace: str = "default",
) -> str:
    """
    Scale a deployment to the specified number of replicas.

    ⚠️ This changes the number of running pod instances. Scaling down
    will terminate pods. Scaling to zero stops all instances.

    Args:
        deployment_name: Name of the deployment (e.g., "checkout-service").
        replicas: Desired number of replicas (0-50).
        namespace: Kubernetes namespace (default: "default").
    """
    # Validate bounds
    if replicas < 0:
        return "❌ Replica count cannot be negative."
    if replicas > 50:
        return "❌ Replica count capped at 50. For higher values, scale manually."

    try:
        result = await k8s.scale_deployment(deployment_name, replicas, namespace)
        return format_scale_result(result)
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(
    annotations={
        "destructiveHint": True,
        "readOnlyHint": False,
        "idempotentHint": False,
    }
)
async def rollback_deployment(
    deployment_name: str,
    namespace: str = "default",
    revision: int | None = None,
) -> str:
    """
    Roll back a deployment to a previous revision.

    ⚠️ This replaces the current pod template with a previous version.
    All pods will be gradually replaced during the rollout.

    Args:
        deployment_name: Name of the deployment.
        namespace: Kubernetes namespace (default: "default").
        revision: Specific revision number to roll back to.
                  If omitted, rolls back to the previous revision.
                  Use get_deployment_history to see available revisions.
    """
    try:
        result = await k8s.rollback_deployment(deployment_name, namespace, revision)
        return format_rollback_result(result)
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
    }
)
async def get_deployment_history(
    deployment_name: str,
    namespace: str = "default",
) -> str:
    """
    Show the revision history of a deployment.

    Lists all revisions with their images, replica counts, and creation dates.
    Use this before rollback_deployment to choose the right revision.

    Args:
        deployment_name: Name of the deployment.
        namespace: Kubernetes namespace (default: "default").
    """
    try:
        history = await k8s.get_deployment_history(deployment_name, namespace)
        return format_deployment_history(history)
    except RuntimeError as e:
        return f"❌ Error: {e}"
```

---

## 5. The Updated Server (Complete)

Here's the complete `src/server.py` with both read and write tools:

```python
# src/server.py - MCP DevOps First Responder (Full: Read + Write)
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
    format_restart_result,
    format_scale_result,
    format_rollback_result,
    format_deployment_history,
)

# Safe logging (never print to stdout)
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
    logger.info("K8s DevOps First Responder ready (read + write mode)")
    try:
        yield
    finally:
        logger.info("K8s DevOps First Responder shutting down")


mcp = FastMCP("K8s DevOps First Responder", lifespan=lifespan)


# ============ READ-ONLY TOOLS ============

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def list_pods(namespace: str = "default") -> str:
    """
    List all pods in a Kubernetes namespace with their status.

    Args:
        namespace: Kubernetes namespace to query (default: "default").
    """
    try:
        pods = await k8s.list_pods(namespace)
        return format_pod_list(pods)
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
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
        pod_name: Name of the pod.
        namespace: Kubernetes namespace (default: "default").
        container: Specific container name (for multi-container pods).
        tail_lines: Number of log lines (default: 100, max: 500).
        previous: If True, get logs from the previous crashed instance.
    """
    tail_lines = max(1, min(tail_lines, 500))
    try:
        logs = await k8s.get_pod_logs(
            pod_name, namespace, container, tail_lines, previous
        )
        header = f"**Logs for {pod_name}"
        if container:
            header += f" (container: {container})"
        if previous:
            header += " [PREVIOUS INSTANCE]"
        header += f" (last {tail_lines} lines):**\n\n"
        return header + f"```\n{logs}\n```"
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def describe_pod(pod_name: str, namespace: str = "default") -> str:
    """
    Full pod details with containers, conditions, and events.

    Args:
        pod_name: Name of the pod to inspect.
        namespace: Kubernetes namespace (default: "default").
    """
    try:
        detail = await k8s.describe_pod(pod_name, namespace)
        return format_pod_detail(detail)
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def list_events(
    namespace: str = "default",
    event_type: str | None = None,
    limit: int = 30,
) -> str:
    """
    List recent Kubernetes events. Warning events often indicate problems.

    Args:
        namespace: Kubernetes namespace (default: "default").
        event_type: Filter: "Warning" or "Normal". Omit for all.
        limit: Max events to return (default: 30).
    """
    limit = max(1, min(limit, 100))
    try:
        events = await k8s.list_events(namespace, event_type, limit)
        return format_event_list(events)
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def list_deployments(namespace: str = "default") -> str:
    """
    List deployments with replica status.

    Args:
        namespace: Kubernetes namespace (default: "default").
    """
    try:
        deployments = await k8s.list_deployments(namespace)
        return format_deployment_list(deployments)
    except RuntimeError as e:
        return f"❌ Error: {e}"


# ============ MUTATING TOOLS (approval required) ============

@mcp.tool(
    annotations={
        "destructiveHint": True,
        "readOnlyHint": False,
        "idempotentHint": False,
    }
)
async def restart_pod(pod_name: str, namespace: str = "default") -> str:
    """
    Restart a pod by deleting it. The controller will recreate it.

    ⚠️ Destructive: terminates the pod and loses in-memory state.

    Args:
        pod_name: Exact pod name (e.g., "checkout-7d8f9-abc12").
        namespace: Kubernetes namespace (default: "default").
    """
    try:
        result = await k8s.delete_pod(pod_name, namespace)
        return format_restart_result(result)
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(
    annotations={
        "destructiveHint": True,
        "readOnlyHint": False,
        "idempotentHint": True,
    }
)
async def scale_deployment(
    deployment_name: str,
    replicas: int,
    namespace: str = "default",
) -> str:
    """
    Scale a deployment to the specified replica count.

    ⚠️ Scaling down terminates pods. Scaling to 0 stops all instances.

    Args:
        deployment_name: Name of the deployment.
        replicas: Desired replica count (0-50).
        namespace: Kubernetes namespace (default: "default").
    """
    if replicas < 0:
        return "❌ Replica count cannot be negative."
    if replicas > 50:
        return "❌ Replica count capped at 50. Scale manually for higher."
    try:
        result = await k8s.scale_deployment(deployment_name, replicas, namespace)
        return format_scale_result(result)
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(
    annotations={
        "destructiveHint": True,
        "readOnlyHint": False,
        "idempotentHint": False,
    }
)
async def rollback_deployment(
    deployment_name: str,
    namespace: str = "default",
    revision: int | None = None,
) -> str:
    """
    Roll back a deployment to a previous revision.

    ⚠️ Replaces current pods with a previous version.

    Args:
        deployment_name: Name of the deployment.
        namespace: Kubernetes namespace (default: "default").
        revision: Specific revision number. Omit for previous revision.
    """
    try:
        result = await k8s.rollback_deployment(deployment_name, namespace, revision)
        return format_rollback_result(result)
    except RuntimeError as e:
        return f"❌ Error: {e}"


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def get_deployment_history(
    deployment_name: str,
    namespace: str = "default",
) -> str:
    """
    Show revision history for a deployment.
    Use before rollback to choose the right revision.

    Args:
        deployment_name: Name of the deployment.
        namespace: Kubernetes namespace (default: "default").
    """
    try:
        history = await k8s.get_deployment_history(deployment_name, namespace)
        return format_deployment_history(history)
    except RuntimeError as e:
        return f"❌ Error: {e}"


# ============ RESOURCE ============

@mcp.resource("k8s://cluster-overview")
async def cluster_overview() -> str:
    """Pods and deployments in the default namespace."""
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
    """Find crashing pods, fetch logs, and request diagnosis."""
    try:
        pods = await k8s.list_pods(namespace)
    except RuntimeError as e:
        return f"Could not list pods: {e}"

    crashing = [p for p in pods if p["restarts"] > 3 or p["status"] != "Running"]
    if not crashing:
        return f"All pods in '{namespace}' appear healthy."

    context = f"# Crash Loop Diagnosis — {namespace}\n\n"
    context += f"**{len(crashing)} unhealthy pod(s).**\n\n"

    for pod in crashing[:5]:
        context += f"---\n## Pod: {pod['name']}\n"
        context += f"- Status: {pod['status']}, Restarts: {pod['restarts']}\n\n"
        try:
            logs = await k8s.get_pod_logs(pod["name"], namespace, tail_lines=30, previous=True)
            context += f"### Previous Logs\n```\n{logs}\n```\n\n"
        except RuntimeError:
            context += "*(No previous logs)*\n\n"
        try:
            logs = await k8s.get_pod_logs(pod["name"], namespace, tail_lines=30)
            context += f"### Current Logs\n```\n{logs}\n```\n\n"
        except RuntimeError:
            pass

    context += (
        "---\n\nAnalyze these pods:\n"
        "1. Root cause of each crash?\n"
        "2. Fix action for each?\n"
        "3. Any cross-pod patterns?"
    )
    return context


# ============ ENTRY POINT ============

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

---

## 6. RBAC: Principle of Least Privilege

In production, the MCP server should use a dedicated ServiceAccount with limited permissions instead of your personal kubeconfig.

### Create a ServiceAccount

```yaml
# k8s-rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: mcp-k8s-agent
  namespace: default
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: mcp-k8s-agent-role
rules:
  # Read operations (Blog 7)
  - apiGroups: [""]
    resources: ["pods", "pods/log", "events"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list", "watch"]
  # Write operations (Blog 8)
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["delete"]  # For restart_pod
  - apiGroups: ["apps"]
    resources: ["deployments", "deployments/scale"]
    verbs: ["patch", "update"]  # For scale and rollback
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: mcp-k8s-agent-binding
subjects:
  - kind: ServiceAccount
    name: mcp-k8s-agent
    namespace: default
roleRef:
  kind: ClusterRole
  name: mcp-k8s-agent-role
  apiGroup: rbac.authorization.k8s.io
```

```bash
kubectl apply -f k8s-rbac.yaml
```

**Key RBAC principles:**

| Principle | Implementation |
|-----------|----------------|
| Minimal verbs | Only `get`, `list`, `delete`, `patch` — no `create` for arbitrary resources |
| No secrets access | The role cannot read Secrets or ConfigMaps |
| No cluster-admin | Only specific resource types are authorized |
| Namespace scoping | Consider using `Role` instead of `ClusterRole` for namespace isolation |

---

## 7. Testing the Write Tools

### Setup: Create Test Deployments

```bash
# Create a healthy deployment with 3 replicas
kubectl create deployment web-app --image=nginx:1.24 --replicas=3

# Wait for pods
kubectl get pods -w
```

### Test 1: Restart a Pod

> "One of the web-app pods seems stuck. Can you restart it?"

Claude will:
1. Call `list_pods` to find the pod name
2. Call `restart_pod("web-app-xxx-yyy")` with the exact name
3. Host shows approval dialog → you click Allow
4. Pod is deleted → Kubernetes creates a replacement

### Test 2: Scale Up

> "We're expecting high traffic. Scale web-app to 5 replicas."

Claude calls `scale_deployment("web-app", 5)`. After approval, you'll see:

```
✅ Deployment Scaled Up

- Deployment: web-app
- Previous replicas: 3
- New replicas: 5

Pods will reach the desired count shortly. Use list_pods to verify.
```

### Test 3: Rollback

```bash
# First, update the image to simulate a bad deploy
kubectl set image deployment/web-app nginx=nginx:1.25
kubectl set image deployment/web-app nginx=nginx:nonexistent
```

> "The web-app deployment is failing after the last update. Show me the history and roll back."

Claude will:
1. Call `get_deployment_history("web-app")` — shows revisions
2. Call `rollback_deployment("web-app")` — rolls back to previous
3. After approval, pods roll out the working image

### Clean Up

```bash
kubectl delete deployment web-app
```

---

## 8. Error Handling Philosophy

Our tools follow a consistent pattern for errors:

| Error Type | How We Handle It | Example |
|-----------|------------------|---------|
| Not found (404) | Clear message | "Pod 'xyz' not found in namespace 'default'" |
| Permission denied (403) | Explain RBAC | "K8s API error: 403 Forbidden" |
| Invalid input | Validate early | "Replica count cannot be negative" |
| API failure | Wrap in ❌ message | "❌ Error: Failed to delete pod" |

The LLM receives clean error messages it can explain to the user instead of raw stack traces.

---

## Key Takeaways

```
 ✅ Human approval for all mutations via destructiveHint annotation
 ✅ Graceful error handling — tools return clean messages, never crash
 ✅ Input validation before K8s API calls
 ✅ RBAC for least privilege access
 ✅ Restart = delete + controller recreates
 ✅ Scale and rollback with safety bounds
 ✅ Deployment history for informed rollback decisions
```

---

## What's Next?

We've covered two domains: databases (Blogs 5-6) and infrastructure (Blogs 7-8). Now let's tackle something different—and introduce one of MCP's most powerful features.

In **Blog 9: Deep Research Browser – Part 1**, we'll build an MCP server that:
- Browses the web headlessly with Playwright
- Extracts content from JavaScript-heavy pages
- Takes screenshots
- Prepares web content for LLM consumption

And in Blog 10, we'll introduce **MCP Sampling**—where the *server* asks the *LLM* for help.

---

## Quick Reference

### New Tools (Blog 8)

| Tool | Annotations | Description |
|------|-------------|-------------|
| `restart_pod` | `destructiveHint: true` | Delete pod for controller to recreate |
| `scale_deployment` | `destructiveHint: true`, `idempotentHint: true` | Change replica count |
| `rollback_deployment` | `destructiveHint: true` | Roll back to previous revision |
| `get_deployment_history` | `readOnlyHint: true` | View revision history |

### RBAC Permissions Required

```yaml
# Read
pods, pods/log, events: get, list, watch
deployments, replicasets: get, list, watch

# Write
pods: delete
deployments, deployments/scale: patch, update
```

---

| [← Blog 7: DevOps First Responder Part 1](../blog-7/blog.md) | [Blog 9: Deep Research Browser Part 1 →](../blog-9/blog.md) |
|:---|---:|
