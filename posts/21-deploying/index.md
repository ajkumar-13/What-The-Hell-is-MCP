# 21 · Deploying to production: containers, scaling, and observability

> **TL;DR.** Revision 2026-07-28 removed sessions from the Model Context Protocol (MCP), which turned deploying a server into an ordinary web-service problem: build a container, run several copies, put a round-robin load balancer in front, and stop thinking about affinity. That means the interesting work moves to the parts nobody writes about, which are tracing, caching, and cost. This post ships a real Dockerfile and compose file for the server built in [Post 05](../05-first-server/index.md), and reports two things measured while writing it that will cost you an afternoon each.
>
> **After reading this you will be able to:**
> - Containerize an MCP server with a pinned base image and a non-root user.
> - Explain to an infrastructure team why no sticky sessions are needed, and what replaced them.
> - Propagate an OpenTelemetry trace across the host, your server, and your backend using `_meta`.
> - Set `ttlMs` and `cacheScope` on list results without leaking one user's catalog to another.

![A deployment topology. On the left, three different hosts each holding their own MCP client. In the middle, one box that terminates Transport Layer Security (TLS), validates the Origin header, and rate limits, feeding a plain round-robin load balancer. On the right, three identical replicas of the same server container, each with an arrow to a shared backend database and an arrow to an observability collector. Between the balancer and the replicas, a struck-through box labeled session store carries a note that there is nothing to store: no session identifier, no handshake, and no in-flight state that survives a request.](diagrams/01-deployment-topology.svg) *The struck-through box is the whole story: the thing that used to make this hard is gone.*

---

## 1. What statelessness actually bought you

Under the old model a remote MCP server was a nuisance to operate. A client opened a connection with an `initialize` handshake, the server minted an `Mcp-Session-Id`, and every subsequent request had to reach the process that remembered that session. The Specification Enhancement Proposal that deleted the handshake (SEP-2575) says why plainly: "A simple stateless load balancer (e.g., L4/L7 round-robin) cannot be used" under those rules.

Three removals compose into the change:

1. **No `Mcp-Session-Id`** (SEP-2567). There is no server-minted identifier for a balancer to pin on.
2. **No `initialize`** (SEP-2575). There is no earlier handshake whose outcome a later request depends on. Every request carries its own `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` in `_meta`.
3. **No cross-request in-flight state** (SEP-2322). A multi round-trip request terminates with a real result and the retry re-sends everything, so the retry can land on any replica.

The release announcement puts the consequence in one sentence: "A remote MCP server that previously needed sticky sessions, a shared session store, and deep packet inspection at the gateway can now run behind a plain round-robin load balancer."

There is a fourth piece that matters to whoever runs your gateway. SEP-2243 mirrors the routing-relevant fields into Hypertext Transfer Protocol (HTTP) headers, so a proxy can route, log, or rate limit on `Mcp-Method` and `Mcp-Name` without parsing a JavaScript Object Notation (JSON) body:

```http
POST /mcp HTTP/1.1
Content-Type: application/json
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: get_weather
```

`Mcp-Method` mirrors `method` and is required on all requests. `Mcp-Name` mirrors `params.name` or `params.uri` and is required on `tools/call`, `resources/read` and `prompts/get`. Both are **REQUIRED** for compliance where they apply, and a server that processes the body **MUST** reject requests where the header and the body disagree, with `400 Bad Request` and JSON-RPC error `-32020` (`HeaderMismatch`). That rule exists precisely because a balancer routing on the header and a server executing on the body are two sources of truth, and disagreement between them is a vulnerability.

## 2. Containerizing, configuration, and secrets

The container for this post lives in [code/21-deploy/](../../code/21-deploy/) and adds two files to the build, a `Dockerfile` and a `compose.yaml`. There is no second copy of the server: the build context is [code/05-first-server/](../../code/05-first-server/), unchanged. That is worth stating because it is the claim the post is making. A stateless server needs no deployment-shaped rewrite.

The build is two stages. Stage one owns `uv` and the toolchain and resolves from the committed lockfile. Stage two receives a virtual environment and nothing else:

```dockerfile
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN useradd --system --no-create-home --shell /usr/sbin/nologin --uid 10001 mcp
WORKDIR /app
COPY --from=build --chown=mcp:mcp /app/.venv /app/.venv
USER 10001
EXPOSE 8000
CMD ["python", "-m", "system_info", "--http", "--host", "0.0.0.0", "--port", "8000"]
```

Five decisions in there are worth naming.

**The base image is pinned to a Debian release**, not to the floating `python:3.12-slim` tag. The stronger form is a digest, and you can produce one rather than invent one: `docker image inspect python:3.12-slim-bookworm --format '{{index .RepoDigests 0}}'`. Paste the result after `FROM`.

**`uv` is pinned too.** An unpinned `:latest` copy of the resolver undoes the reproducibility the lockfile was there to provide.

**The user has a fixed numeric id.** `USER 10001` rather than `USER mcp`, so an orchestrator enforcing a run-as-non-root policy can check it without reading `/etc/passwd` inside the image.

**`PYTHONUNBUFFERED=1` is not cosmetic.** Diagnostics go to standard error, and a buffered stream loses the last few lines of a crashing process, which are the ones you wanted.

**Both stages use `/app`.** A virtual environment records its own absolute path, so copying `/src/.venv` into `/app/.venv` in the runtime stage would leave every script in `.venv/bin` with a shebang pointing at a directory that no longer exists. Building at the final path costs nothing and removes the whole class of problem.

Configuration is the boring part and it should stay boring. Read it from the environment at startup, fail loudly if something required is missing, and never read it again. Secrets are the part people get wrong: put the *path* to a secret in the environment and the secret itself in a mounted file. `docker inspect` prints a container's environment, orchestrators log it, and every child process inherits it. A file has an owner and a mode.

## 3. Behind a load balancer, with nothing to pin

Scaling the compose file is one flag:

```bash
docker compose up --build --scale system-info=3
```

Three containers, no shared store, no affinity configured anywhere. Requests land wherever they land. Nothing in the protocol notices.

Old clients will still knock. The specification says what to do with each of their habits:

| A pre-2026-07-28 client sends | Your server should |
|---|---|
| `Mcp-Session-Id` on a request | Ignore it, and do not mint or echo session identifiers |
| `Last-Event-ID` | Ignore it; streams are not resumable |
| HTTP `GET` or `DELETE` to the MCP endpoint | Respond `405 Method Not Allowed` |

The last one is real, not theoretical: a plain `GET` against this project's server returns `405`, which is exactly what the specification asks for.

One consequence of dropping resumability lands on the client and is easy to forget when you are sizing timeouts. There is no redelivery any more: "A broken response stream loses the in-flight request; clients **MUST** re-issue it as a new request with a new request ID." Idempotency is therefore your problem, not the transport's. A tool that charges a card should take a caller-supplied key, because the client is allowed to send the same call twice after a dropped connection.

Two more knobs for whoever owns the proxy. Servers **SHOULD** send `X-Accel-Buffering: no` when they open a Server-Sent Events stream, or a reverse proxy will buffer the events and your progress notifications arrive in one lump at the end. And long-lived `subscriptions/listen` streams are kept alive with Server-Sent Events comment lines, a bare `:` on its own line, which clients must ignore rather than treat as malformed.

## 4. Two edge cases measured while writing this

Both are claims about the software development kit's behavior rather than about any code in this series, which is the kind of claim that goes stale quietly: a point release could make either one wrong without anything failing. So both are regenerated by the capture script behind this series, and if its output and this section ever disagree, this section is the one that is wrong.

**The trailing slash costs a round trip, or breaks the client.** The endpoint is `/mcp`. Add a slash and the Python software development kit (SDK) redirects rather than serves, because its router treats the two paths as different routes. Measured against this series' own server on `mcp==2.0.0b2`:

```
POST /mcp    ->  200 OK
POST /mcp/   ->  307 Temporary Redirect
             location: http://127.0.0.1:8765/mcp
```

Clients that follow redirects pay an extra round trip on every single request. Clients that do not follow redirects on a `POST`, which is a defensible default because redirecting a body is historically messy, simply fail. And notice the `Location`: it is `http`, not `https`. Behind a terminator that does not rewrite it, a redirect chain can walk a client off TLS. Publish the path without the slash, and if you control the ingress, rewrite `/mcp/` to `/mcp` there rather than shipping the redirect.

**Binding to `0.0.0.0` silently turns off `Origin` validation.** The transports page is unambiguous: servers "**MUST** validate the `Origin` header on all incoming connections to prevent DNS rebinding attacks", where DNS is the Domain Name System, and where `Origin` is present and invalid, "servers **MUST** respond `403 Forbidden`". The Python SDK auto-arms that protection only when the bind address is `127.0.0.1`, `localhost` or `::1`; otherwise the middleware is constructed with protection disabled, and the source comment says why: "for backwards compatibility". A container has to bind `0.0.0.0`.

Same request, same `Origin: https://evil.example` header, `mcp==2.0.0b2`:

| Bind address | Response |
|---|---|
| `127.0.0.1` | `403 Forbidden` |
| `0.0.0.0` | `200 OK` |

Close it at the ingress, where you are already terminating TLS, or close it in the server:

```python
from mcp.server.transport_security import TransportSecuritySettings

mcp.run(
    "streamable-http", host="0.0.0.0", port=8000,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["mcp.example.com"],
        allowed_origins=["https://mcp.example.com"],
    ),
)
```

Get `allowed_hosts` wrong and every request returns `421 Misdirected Request`, which is a loud failure and therefore the good kind.

## 5. Health and readiness

The protocol defines no health check, and it should not: liveness is a deployment concern. What it gives you instead is a route escape hatch in the SDK:

```python
@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> Response:
    return JSONResponse({"status": "ok"})
```

Custom routes are never authenticated, by design. That is the right default for a liveness probe and the wrong default for anything else, so put only "the process is up" behind it.

Liveness and readiness are different questions and conflating them causes restart loops. Liveness asks whether the process should be killed. Readiness asks whether it should receive traffic. A server whose database is briefly unreachable is not dead, it is not ready, and restarting it will not help. Give liveness a check that touches nothing external, and give readiness one that touches the dependency you cannot serve without.

The container in [code/21-deploy/](../../code/21-deploy/) has no health route because the server it wraps does not define one, so its `HEALTHCHECK` opens a socket and closes it. That is honest about what it proves: something is listening. When your server has real dependencies, add the route.

## 6. Caching list results

New in this revision, and easy to miss because it does not look like a protocol feature. `CacheableResult` (SEP-2549) adds two required fields:

```typescript
export interface CacheableResult extends Result {
  ttlMs: number;                        // REQUIRED, minimum 0
  cacheScope: "public" | "private";     // REQUIRED
}
```

Servers **MUST** include them on results with `resultType: "complete"` returned by exactly six methods: `server/discover`, `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`, and `resources/read`. `tools/call`, `prompts/get`, and `completion/complete` carry neither.

On the wire it is two extra keys:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "resultType": "complete",
    "tools": [],
    "ttlMs": 300000,
    "cacheScope": "public"
  }
}
```

`ttlMs` has the semantics of `Cache-Control: max-age`. Zero means immediately stale. A positive value means the client **SHOULD** treat the result as fresh for that many milliseconds. A negative value is treated as zero, and an absent value defaults to zero.

`cacheScope` is the field that can hurt you. `"public"` says the response holds nothing user-specific and any shared gateway may serve it to anybody. `"private"` says caches **MUST NOT** be shared across authorization contexts, so a different access token requires a different cache entry. The specification attaches a warning you should read twice: "Servers MUST be aware that responses with a `public` `cacheScope` may be shared between callers even if the Result is coming from an authenticated endpoint", and servers "MUST apply appropriate per-primitive access controls, and MUST NOT rely on `cacheScope` alone to prevent unauthorized access". If your `tools/list` is filtered by the caller's scopes, it is `"private"`, and marking it `"public"` hands one user's catalog to the next.

Three more rules worth pinning to the wall. Cache keys are the method plus the parameters that affect the result. Results from a multi round-trip retry, meaning anything carrying `inputResponses` or `requestState`, **MUST NOT** be cached at all. And a notification invalidates a fresh cached response immediately, so caching and `subscriptions/listen` are complementary rather than alternatives.

A word of caution about what a client will do with a generous `ttlMs`. The specification tells clients not to treat it as a polling interval, and says that implementations which do poll **MUST** apply jitter and backoff. Some will not. A five-minute time to live on a catalog that changes twice a day is a reasonable number; five seconds is an invitation.

## 7. Tracing: three unprefixed keys in `_meta`

![A four-lane span waterfall. The top lane is the host, whose span covers the whole request. Beneath it the client's span, then the MCP server's span, then a shorter span for the backend database call the server makes. All four bars are annotated with the same 32-character trace identifier, and the parent span identifier changes at each hop. To the right, a JSON callout shows the request _meta block carrying traceparent, tracestate and baggage next to the two required protocol keys, with a note that the three tracing keys are the only unprefixed reserved keys in the protocol, and that riding in _meta rather than an HTTP header is what makes them work on the stdio transport as well.](diagrams/02-trace-across-hops.svg) *One trace identifier, four spans, and a carriage that survives a transport with no headers.*

Every reserved `_meta` key in MCP is a reverse-domain name, `io.modelcontextprotocol/something`. Three keys are not, and that is deliberate. SEP-414 puts World Wide Web Consortium (W3C) Trace Context into `_meta` under its own standard names:

| Key | Format | Carried on |
|---|---|---|
| `traceparent` | W3C Trace Context | any message |
| `tracestate` | W3C Trace Context | any message |
| `baggage` | W3C Baggage | any message |

The specification explains the exception in one line: "This exception exists to maintain compatibility with existing implementations and OpenTelemetry semantic conventions for MCP." Renaming them to `io.modelcontextprotocol/traceparent` would have broken every existing instrumentation library for no gain.

In a request they sit beside the two required keys:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "get_weather",
    "arguments": { "location": "Seattle, WA" },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {},
      "traceparent": "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
      "tracestate": "congo=t61rcWkgMzE",
      "baggage": "userId=alice,serverNode=DF%2028"
    }
  }
}
```

The `traceparent` value has four dash-separated parts: the version, a 32-character trace identifier, a 16-character parent span identifier, and flags. The trace identifier is the same across every hop; the parent span identifier changes at each one. That is what lets a single waterfall show the host's turn, your server's turn, and the database call your server made underneath it.

The interesting property is where they live. Because they ride in `_meta` and not in an HTTP header, they work unchanged on stdio, which has no header layer at all. A locally spawned server can join the host's trace. That is not something the old `logging/setLevel` world could do.

Two cautions. `baggage` is a key-value list that propagates across service boundaries by design, so anything you put in it will end up in your vendor's storage and in every downstream system. It is not a place for anything you would not put in a log line. And these keys are optional, so instrument for their absence: if no `traceparent` arrives, start a root span rather than dropping the request on the floor.

## 8. Logging to standard error

Protocol logging is deprecated as of `2026-07-28` (SEP-2577), and the deprecated features registry names the replacement in the migration column: "Log to `stderr` for stdio transports; use OpenTelemetry for observability." The `logging/setLevel` method is not deprecated but removed outright, with no window.

What survives is narrower than most people expect. A server may only emit `notifications/message` for a request whose `_meta` carried `io.modelcontextprotocol/logLevel`, and if the key is absent the server **MUST NOT** emit any. Both the notification and the key carry deprecation markers. Treat the protocol log channel as a thing you will meet in older code, not a thing you build on.

So: write structured lines to standard error and let the platform collect them. The server in [code/05-first-server/](../../code/05-first-server/) already does the important half, and does it for a reason that outlives this post:

```python
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
```

Under stdio, standard output *is* the protocol channel, so a single `print()` in a tool body corrupts a JSON-RPC frame. Configuring the root logger onto standard error at import time is what stops a library you depend on from doing that on your behalf. Over HTTP the stakes are lower but the habit is the same, and it means one logging setup works on both transports.

Two fields make those lines worth collecting. Log the trace identifier from `traceparent`, so a log line and a span can be joined. Log `Mcp-Method` and `Mcp-Name`, because "a request failed" is useless and "`tools/call` of `run_query` failed" is a bug report.

## 9. Rate limiting and abuse

A remote MCP server is a public interface driven by a model, which means the traffic shape is unlike a human user interface. A single conversation turn can produce a burst of parallel tool calls, and a model in a retry loop can produce a lot of them.

Limit in the layer that already sees the request. Thanks to SEP-2243 your gateway can read `Mcp-Method` and `Mcp-Name` without touching the body, so per-tool limits do not require an application-aware proxy. Give `tools/call` on an expensive tool a different budget from `tools/list`, and give discovery a generous one, because `server/discover` and the list methods are cacheable and a well-behaved client will not ask often.

Two protocol-shaped notes. First, an over-limit response is an HTTP concern, not a JSON-RPC one; the specification allocates `-32020` through `-32099` exclusively to itself and forbids emitting undefined codes from that range, so do not invent a rate-limit code inside it. Errors of your own belong outside the JSON-RPC reserved range entirely. Second, a tool that fails because of a limit you imposed is usually an execution error rather than a protocol error, which means a result with `isError: true` and a message the model can act on, not a JSON-RPC error the model never sees.

## 10. Where the cost actually goes

![Four cost centers laid out along the path of one request, each with the thing that drives it and the lever the server author actually controls. Model tokens sit at the host end and are driven by the size of every tool description and every tool result. Server compute sits in the middle and is driven by what the tool body does. Egress and backend cost sit at the far end. A banner across the diagram states that the boxes are not drawn to scale, that no proportions are claimed, and that the only honest ranking is the one measured on a specific workload.](diagrams/03-where-cost-goes.svg) *Not to scale, deliberately. The point is the ordering of the levers, not a pie chart.*

The instinct is to look at compute, because that is the line item with your name on it. It is usually the smallest of the four.

**Every tool description is billed on every turn.** A host sends the model the full catalog of tools it can call, and it does that for each request in the conversation. A three-hundred-word description on a tool the model never picks is a tax paid continuously. This is the strongest argument for the schema discipline in [Post 06](../06-tools-in-depth/index.md), and it is an argument about money as well as accuracy.

**Every tool result is billed too, and it is billed at whatever size you returned.** Returning a whole document where a summary would do is the single largest lever most server authors have. [Post 17](../17-research-browser/index.md) is entirely about this, and the point generalizes: a resource link the host can choose to follow costs almost nothing, and the same content inlined costs its full length every turn it stays in context.

**Caching moves work off both of the above.** A `tools/list` with a sensible `ttlMs` and a `cacheScope` of `"public"` stops a shared gateway asking again, and it also stabilizes the bytes the host sends the model, which matters for prompt caching. A catalog that is byte-for-byte identical between turns can be cached by the model provider; one that reorders itself cannot.

**Compute is mostly idle waiting.** An MCP server spends its time on network calls to something else. Size the container for concurrency rather than for processing, and set a memory limit rather than guessing a large one.

No numbers appear in this section, and that is on purpose. The proportions depend entirely on the workload, and a figure quoted from someone else's deployment is worse than no figure at all. Instrument the trace from section 7, put the token counts from your host next to the span durations from your server, and you will have the real ranking in a day.

---

## Common pitfalls

- **Publishing the endpoint with a trailing slash.** `POST /mcp/` answers `307`, not `200`. Clients that follow redirects pay a round trip on every call and clients that do not follow them on a `POST` fail outright, and the `Location` header can walk a client off TLS.
- **Assuming the container validates `Origin`.** Binding `0.0.0.0`, which a container must, skips the SDK's automatic DNS-rebinding protection. Validate at the ingress or pass `TransportSecuritySettings` explicitly.
- **Marking a filtered list `"public"`.** If `tools/list` differs per caller it is `"private"`. A shared cache will otherwise hand one user's catalog to the next, and the specification says `cacheScope` was never an access control in the first place.
- **Prefixing the tracing keys.** The specification names `traceparent`, `tracestate` and `baggage` as an explicit exception to the prefix rule, so they stay bare. Writing `io.modelcontextprotocol/traceparent` produces a key nothing reads.
- **Leaving a health route authenticated, or unauthenticated by accident.** `@mcp.custom_route` is never authenticated. That is correct for liveness and wrong for anything that reveals configuration, dependency names, or version detail.
- **Debugging with `curl` and omitting `_meta`.** The two required keys are `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities`, and a request without them earns `400` with `-32602`. On `mcp==2.0.0b2` the server also rejects a request missing `io.modelcontextprotocol/clientInfo`, which the specification only makes a **SHOULD**, so a hand-written probe needs all three.
- **Restarting on a readiness failure.** A dependency being briefly unavailable is not a reason to kill a healthy process. Separate the two probes and only one of them should be able to trigger a restart.

---

## Further reading

- Specification, *"Transports: Streamable HTTP"*, revision 2026-07-28.
- Specification, *"Caching"* and *"Deprecated features"*, revision 2026-07-28.
- SEP-2567 (remove sessions), SEP-2575 (remove `initialize`), SEP-2243 (routing headers), SEP-2549 (`CacheableResult`), SEP-414 (trace context in `_meta`), SEP-2577 (deprecate roots, sampling, logging).
- W3C, *"Trace Context"* and *"Baggage"*; OpenTelemetry Python documentation.
- `uv` documentation, for the lockfile and `uv sync --frozen`.

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 22 — Publishing: the registry, `server.json`, and MCPB bundles](../22-publishing/index.md)**: now that it runs somewhere, make it something a stranger can install.
- **[Post 20 — Authorization: OAuth 2.1 for MCP servers](../20-authorization/index.md)**: the token validation that belongs in front of everything deployed here.
