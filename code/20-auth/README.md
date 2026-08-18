# 20-auth

The OAuth 2.1 resource server behind [post 20](../../posts/20-authorization/index.md), plus a
toy authorization server so the three-party flow can be run end to end.

Post 20 says "Every response shown was produced by running the code". This project is that
code. Every status line, header string, and metadata document the post quotes has a test here
that asserts it, so a reader can check the post rather than trust it, and a future software
development kit (SDK) release that changes any of it fails the suite instead of quietly making
the post wrong.

| File | What it is | Post |
|---|---|---|
| `src/auth_demo/verifier.py` | `StaticTokenVerifier`, `JWTVerifier`, and the audience check | [20](../../posts/20-authorization/index.md) §5, §9 |
| `src/auth_demo/app.py` | The protected `MCPServer`, in two configurations | §4, §9, §10 |
| `src/toy_as/provider.py` | The authorization server provider. **A fixture. Read its header.** | §2, §7, §8 |
| `src/toy_as/app.py` | Those routes assembled by `create_auth_routes` | §4 |
| `tests/test_resource_server.py` | Every figure post 20 prints | §4, §9, §10 |
| `tests/test_flow.py` | The whole exchange, in process | §4 |

## The part to read first

`src/toy_as/provider.py` opens with a boxed warning, and it is not decoration. The
authorization server here has no user authentication, no consent screen, an in-memory store,
and a symmetric signing key checked into a public repository. It exists so that
`tests/test_flow.py` is a real authorization code exchange instead of a hand-written JWT.

Post 20 §2 is blunt about the real lesson: you are almost certainly not writing an
authorization server, and if your MCP server starts storing passwords you have accidentally
started writing one. Point at an existing one instead.

## Requirements

Python 3.10 or newer, and [uv](https://docs.astral.sh/uv/).

## Install

```bash
uv sync --extra dev
```

The `mcp` dependency is **pinned exactly** to `2.0.0b2`. The `WWW-Authenticate` header text
this project asserts on is a detail the SDK is free to change between releases, and floating
the pin would make post 20 wrong without anything failing.

## Run

```bash
uv run python -m auth_demo              # resource server, 127.0.0.1:8123
uv run python -m auth_demo --jwt        # the same, trusting the toy authorization server
uv run python -m toy_as                 # toy authorization server, 127.0.0.1:9123
```

With the resource server up, the refusals from post 20 §10 and the two
discovery probes from §4:

```bash
curl -i -X POST http://127.0.0.1:8123/mcp                                  # 401
curl -i -X POST http://127.0.0.1:8123/mcp -H "Authorization: Bearer nope"  # 401
curl -i -X POST http://127.0.0.1:8123/mcp -H "Authorization: Bearer weak-token"  # 403
curl -i http://127.0.0.1:8123/.well-known/oauth-protected-resource/mcp     # 200
curl -i http://127.0.0.1:8123/.well-known/oauth-protected-resource         # 404
```

## Test

```bash
uv run pytest
```

```
21 passed, 1 warning
```

The suite binds no socket. `streamable_http_app()` returns a Starlette application and the
tests speak to it over ASGI with `httpx2.ASGITransport`, so the whole three-party flow runs in
one process with no browser and nothing to wait for.

Two things worth knowing about the test files:

- **No `yield` fixtures.** The application lifespan and the client each own an anyio task
  group, and a task group must be exited by the task that entered it. An async yield fixture
  tears down in a different task, and every test fails with `Attempted to exit cancel scope in
  a different task than it was entered in`. Each test opens its own client with `async with`
  in the test body. Post 12 §4 covers this; `code/05-first-server/tests/` meets the same rule.
- **`clientInfo` is required in practice.** The specification lists it as SHOULD, but
  `mcp==2.0.0b2` rejects a request whose `params._meta` omits it, with `-32602` and the
  message "params._meta must carry the reserved protocol-version, client-info and
  client-capabilities envelope keys". Leave it out while debugging a token and the failure
  arrives as a `400` that says nothing about authorization.

## Two findings this project produced

**The issuer's trailing slash.** `build_metadata` publishes the issuer after pydantic's
`AnyHttpUrl` has normalized it, and normalization appends a slash to a URL with no path. The
first version of `toy_as` minted the `iss` claim from the un-normalized string, so the
published metadata said `http://127.0.0.1:9123/` and every token said `http://127.0.0.1:9123`.
RFC 9207 comparison is exact, so a correct client would have rejected every token. It only
bites a path-less issuer, which is the common case.
`test_the_published_issuer_and_the_minted_iss_are_byte_identical` pins it, and it asserts the
two values against each other rather than against a shared constant, because a shared constant
would have been wrong in both places at once.

**One SDK warning, not ours.** The suite emits one `IncompleteFieldDefinitionWarning` from
`pydantic_settings` about an unresolved forward reference on a `lifespan` field inside the SDK.
Nothing in this project can fix it and nothing here depends on it.
