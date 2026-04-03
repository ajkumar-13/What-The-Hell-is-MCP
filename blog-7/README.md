# Blog 7: DevOps First Responder – Part 1 (Read & Diagnose)

MCP server for Kubernetes cluster diagnostics.

## Setup

```bash
cd mcp-k8s-agent
uv init
uv add "mcp[cli]" kubernetes
```

## Prerequisites

- Python 3.10+
- A running Kubernetes cluster (`kubectl cluster-info` works)
- kubeconfig configured

## Run

```bash
uv run python -m src.server
```

## Tools

| Tool | Description |
|------|-------------|
| `list_pods` | List pods with status, restarts, age |
| `get_pod_logs` | Read logs from any pod/container |
| `describe_pod` | Detailed pod info with events |
| `list_events` | Recent cluster events |
| `list_deployments` | Deployment replica status |
