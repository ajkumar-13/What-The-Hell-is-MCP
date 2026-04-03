# Blog 8: DevOps First Responder – Part 2 (Fix & Remediate)

Extends the MCP K8s agent from Blog 7 with mutating operations and human-in-the-loop approval.

## What This Blog Adds

- **restart_pod** — Delete a pod so its controller recreates it
- **scale_deployment** — Change the number of replicas
- **rollback_deployment** — Roll back to a previous revision
- **get_deployment_history** — View revision history before rollback

## Key Concepts

- `destructiveHint: true` / `readOnlyHint: false` annotations for write tools
- Human approval via Host UI before any mutation executes
- Kubernetes RBAC (ServiceAccount + ClusterRole) for least-privilege access
- Input validation and clear error messages for the LLM

## Project Structure (same as Blog 7, with additions)

```
mcp-k8s-agent/
├── pyproject.toml
├── k8s-rbac.yaml          ← NEW: RBAC manifest
└── src/
    ├── __init__.py
    ├── server.py           ← Updated with write tools
    ├── k8s_client.py       ← Extended with mutating operations
    └── formatters.py       ← New format helpers for mutations
```

## Quick Test

```bash
# 1. Create a test deployment
kubectl create deployment web-app --image=nginx:1.24 --replicas=3

# 2. Start the MCP server (from Blog 7 setup)
cd mcp-k8s-agent && uv run mcp-k8s-agent

# 3. Try: "Scale web-app to 5 replicas"
#    → Host shows approval dialog → Allow → done

# 4. Trigger a rollback scenario
kubectl set image deployment/web-app nginx=nginx:nonexistent
# Try: "web-app is failing, show history and roll back"

# 5. Clean up
kubectl delete deployment web-app
```

## Prerequisites

- Blog 7 completed (mcp-k8s-agent project set up)
- Kubernetes cluster with kubectl access
- Claude Desktop or another MCP host that supports tool approval dialogs

## Navigation

| Previous | Next |
|----------|------|
| [Blog 7: DevOps Part 1](../blog-7/blog.md) | [Blog 9: Deep Research Browser Part 1](../blog-9/blog.md) |
