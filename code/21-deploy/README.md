# 21-deploy

Two files that containerize [`code/05-first-server/`](../05-first-server/) and
nothing else. There is no second copy of the server here, and no fork of it.
The whole point of post 21 is that a 2026-07-28 server is an ordinary
stateless web service, and an ordinary web service does not need its code
rearranged to be deployed.

| File | What it is |
|---|---|
| `Dockerfile` | Two-stage build. uv and the toolchain live in stage one; a virtual environment and a non-root user are all that reach stage two. |
| `compose.yaml` | One service, published on loopback, with the container hardening flags spelled out. |

## Build and run

The build context is the *other* directory. From the repository root:

```bash
docker build -f code/21-deploy/Dockerfile -t mcp-system-info:0.2.0 code/05-first-server
docker run --rm -p 127.0.0.1:8000:8000 mcp-system-info:0.2.0
```

Or, from this directory, let Compose sort the paths out:

```bash
docker compose up --build
```

Scale it, which is the interesting part:

```bash
docker compose up --build --scale system-info=3
```

Three containers, one published port range, no shared store, no sticky
sessions, no session affinity configured anywhere. Under revision 2026-07-28
every request carries its own protocol version and its own capabilities, so
there is nothing for a load balancer to pin to.

## Talking to it

The endpoint is `POST http://127.0.0.1:8000/mcp`. Every request needs the
`MCP-Protocol-Version` and `Mcp-Method` headers, and a `_meta` block carrying
the reserved envelope keys:

```bash
curl -i -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/list' \
  --data '{
    "jsonrpc": "2.0", "id": 1, "method": "tools/list",
    "params": { "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "curl", "version": "0" },
      "io.modelcontextprotocol/clientCapabilities": {}
    } }
  }'
```

**Do not add a trailing slash.** `POST /mcp` answers `200`. `POST /mcp/`
answers `307 Temporary Redirect` with a `Location` of `/mcp`, which costs a
round trip for clients that follow redirects and fails outright for clients
that do not. Both were measured against this project's server on
`mcp==2.0.0b2`.

## Three things this Dockerfile decides for you

**A pinned base, and how to pin it harder.** `python:3.12-slim-bookworm`
names a Debian release rather than floating on `slim`. The stronger form is a
digest, and you do not have to invent one:

```bash
docker image inspect python:3.12-slim-bookworm --format '{{index .RepoDigests 0}}'
```

Paste the result after `FROM` and the build becomes byte-reproducible.

**A non-root user with a fixed uid.** `USER 10001` rather than `USER mcp`, so
a Kubernetes `runAsNonRoot` admission check can verify it without resolving
`/etc/passwd` inside the image.

**Liveness is a socket connect, not a route.** This server publishes no health
route, so the `HEALTHCHECK` proves only that something is listening. That is
the honest limit of what can be checked here. When your server has real
dependencies, give it a route of its own:

```python
@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> Response:
    return JSONResponse({"status": "ok"})
```

Custom routes are never authenticated, by design. Put the liveness answer
there and keep everything that could leak on the MCP endpoint.

## The Origin gap, measured

Binding to `0.0.0.0` is not optional inside a container, and it turns off a
protection you may not know you had. The Python SDK auto-arms DNS-rebinding
protection only when the bind address is `127.0.0.1`, `localhost` or `::1`;
otherwise the middleware is constructed with protection disabled "for
backwards compatibility". The specification says servers **MUST** validate the
`Origin` header, so the default in a container does not meet it.

Measured on `mcp==2.0.0b2`, same request, same header
`Origin: https://evil.example`:

| Bind address | Response |
|---|---|
| `127.0.0.1` | `403 Forbidden` |
| `0.0.0.0` | `200 OK` |

Close it in one of two places. Either validate `Origin` at the reverse proxy,
which is where you are already terminating TLS, or pass the settings
explicitly in the server:

```python
from mcp.server.transport_security import TransportSecuritySettings

mcp.run(
    "streamable-http",
    host="0.0.0.0",
    port=8000,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["mcp.example.com"],
        allowed_origins=["https://mcp.example.com"],
    ),
)
```

Get `allowed_hosts` wrong and every request returns `421 Misdirected Request`,
which is a much better failure than silently accepting anything.

## Two things left out on purpose

**No `.dockerignore`.** Every `COPY` here names its source explicitly, so the
context is small already. The one thing that does ride along is
`src/system_info/__pycache__/` if you have run the tests on the host. Add
`Dockerfile.dockerignore` next to the Dockerfile with `**/__pycache__` in it
if that bothers you; BuildKit reads a per-Dockerfile ignore file.

**No TLS, no cloud provider.** Terminating TLS, choosing a registry, and
picking a host are all outside the protocol and change every year. What does
not change is the shape above: one stateless process, any number of copies, no
affinity.
