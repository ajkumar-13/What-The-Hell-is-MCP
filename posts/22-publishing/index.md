# 22 · Publishing: the registry, `server.json`, and MCPB bundles

> **TL;DR.** A Model Context Protocol (MCP) server nobody can install is a private script, and the two mechanisms that fix that are a registry entry described by a `server.json` file and a bundle file a user can double-click. Both are worth an hour, and both come with caveats large enough that this post leads with them: the registry is in preview rather than generally available, and its own moderation policy says it will not remove servers with known security vulnerabilities. This post publishes the server from [Post 05](../05-first-server/index.md) for real, and reports three places where the primary sources still contradict each other.
>
> **After reading this you will be able to:**
> - Write a `server.json` that validates against the current schema and matches your package metadata.
> - Prove you own a namespace, by GitHub identity, a Domain Name System record, or a file on your own domain.
> - Automate publishing from continuous integration using GitHub OpenID Connect, with no stored secret.
> - Say precisely what the registry promises a consumer, which is much less than people assume.

![Four routes from one repository to a running server on a user's machine. Route one is a package registry such as PyPI plus a metadata entry in the MCP Registry, which an aggregator indexes and a host resolves into a uvx command. Route two is a remote server, where the registry entry carries a URL and the host simply connects to it. Route three is an MCPB bundle attached to a GitHub release, which a user double-clicks into a desktop application. Route four is the baseline: a git clone and a hand-edited configuration file, which works and reaches nobody. Each route is annotated with what the user has to do and what the registry actually knows about it.](diagrams/01-distribution-paths.svg) *Four routes out of one repository. The registry stores metadata about three of them and hosts none of them.*

---

## 1. The distribution options, compared

Everything up to here in the series ended the same way: clone the repository, run `uv sync`, paste an absolute path into a configuration file. That works, and it reaches nobody. Four routes exist and they are not alternatives so much as layers.

| Route | What the user does | Who hosts the code |
|---|---|---|
| Clone and configure by hand | Reads your README, edits a JavaScript Object Notation (JSON) file | Your git host |
| Package registry, plus a registry entry | Their host resolves `uvx mcp-system-info` or similar | PyPI, npm, a container registry |
| Remote server | Their host connects to a Uniform Resource Locator (URL) | You |
| MCPB bundle | Double-clicks a `.mcpb` file | A GitHub or GitLab release |

The MCP Registry sits across the middle two and the last one, and the most useful thing to understand about it early is what it is not. From its own quickstart: "The MCP Registry only hosts metadata, not artifacts." Your code still lives on PyPI, npm, a container registry, or a release page. The registry is an index that says "this name maps to that package, and this person proved they own both".

The second surprise is who it is for: "The MCP Registry is intended to be consumed primarily by downstream aggregators, such as MCP server marketplaces. … The MCP Registry is **not** intended to be directly consumed by host applications." You are publishing to a wholesale index, not to a shop front.

## 2. Read this before you rely on it

Two caveats, and neither is small.

**The registry is in preview, not generally available.** Every registry documentation page carries the same banner, verbatim:

> The MCP Registry is currently in preview. Breaking changes or data resets may occur before general availability.

"Data resets" means what it says. The application programming interface (API) has been frozen at **v0.1 since 2025-10-24**, which is a stability commitment for integrators, and the README frames it as exactly that: "For the next month or more, the API will remain stable with no breaking changes, allowing integrators to confidently implement support." The freeze is about the shape of the interface, not a promise that your entry survives.

**The moderation caveat is the headline.** The moderation policy opens cheerfully and then says the important part:

> The MCP Registry **does not** make guarantees about moderation, and consumers should assume minimal-to-no moderation.

It removes four things: illegal content, malware, spam, and non-functioning servers. It explicitly does not remove these, quoted in full because the list is the caveat:

> * Low-quality or buggy servers
> * Servers with security vulnerabilities
> * Servers that do the same thing as other servers
> * Servers that provide or contain adult content

The second bullet is the one to sit with. Presence in the registry is not a security signal. It is a claim that somebody proved control of a namespace, and nothing more. The terms of service say the same thing in legal register: the registry is provided "as is" with no warranties, and "we highly recommend that you evaluate each MCP server and its suitability for your intended use case(s) before deciding whether to use it." [Post 19](../19-security/index.md) is the reason that sentence matters.

There is a corollary for publishers. **You cannot unpublish.** The frequently asked questions answer the question "can I remove a server" with, verbatim, "Currently, no." What you can change is a `status` field, to `deprecated` or `deleted`, and even a deleted server's metadata "remains accessible via the MCP Registry API". Treat publishing as append-only, and do not put anything in a `server.json` you would need to retract.

## 3. `server.json`, field by field

The schema is dated, and the date is part of the URL:

```
https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
```

That is a JSON Schema draft-07 document, and `2025-12-11` is the current version. Three fields are required at the top level: `name`, `description`, and `version`.

| Field | Required | Constraint worth knowing |
|---|---|---|
| `name` | yes | Reverse domain name with exactly one `/`, 3 to 200 characters |
| `description` | yes | 1 to **100** characters. This is the one that surprises people |
| `version` | yes | Your server's own release version, not a range |
| `title` | no | A display name, up to 100 characters |
| `repository` | no | `{ url, source }` required if present, plus optional `id` and `subfolder` |
| `packages` | no | Where the artifact lives. `registryType`, `identifier` and `transport` are each required |
| `remotes` | no | For a hosted server: a type, a URL, and optional template variables |

A hundred characters is short. It is roughly this sentence and no more, so write the description as a single clause naming what the server does, and put the prose in the README.

Here is the real file for this series' own server, committed at [code/05-first-server/server.json](../../code/05-first-server/server.json):

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.ajkumar-13/system-info",
  "title": "System Info",
  "description": "Reports CPU, memory, disk, and process facts about the machine it runs on",
  "version": "0.2.0",
  "websiteUrl": "https://github.com/ajkumar-13/What-The-Hell-is-MCP",
  "repository": {
    "url": "https://github.com/ajkumar-13/What-The-Hell-is-MCP",
    "source": "github",
    "subfolder": "code/05-first-server"
  },
  "packages": [
    {
      "registryType": "pypi",
      "registryBaseUrl": "https://pypi.org",
      "identifier": "mcp-system-info",
      "version": "0.2.0",
      "runtimeHint": "uvx",
      "transport": { "type": "stdio" }
    }
  ]
}
```

Two details in there are load-bearing. `identifier` is the **package** name on PyPI, `mcp-system-info`, which is not the same string as the **server** name in the registry. And `subfolder` exists because this project lives inside a larger repository, which is common and often left out.

One field to notice by its absence. `transport.type` accepts `stdio`, `streamable-http`, and `sse`. That third value is still first-class in the registry schema even though the HTTP with Server-Sent Events transport is deprecated at the specification layer as of `2026-07-28`, and the registry documentation carries no deprecation note. Do not pick it for something new.

A last quotable from the schema itself, on the `Argument` type, because it explains why hosts are cautious about what they will run:

> Arguments construct command-line parameters that may contain user-provided input. This creates potential command injection risks… a malicious argument value like ';rm -rf ~/Development' could execute dangerous commands.

## 4. Namespaces, and proving you own one

The `name` field is a namespace plus a server name, and you have to prove the namespace. Two families:

| Authentication | Name format |
|---|---|
| GitHub | `io.github.username/*` or `io.github.orgname/*` |
| Domain | `com.example.*/*` |

GitHub is a login. Domain ownership is a public key you publish, either as a DNS `TXT` record:

```
example.com. IN TXT "v=MCPv1; k=ed25519; p=${PUBLIC_KEY}"
```

or as a file at `https://example.com/.well-known/mcp-registry-auth` containing only what is inside the quotes. Ed25519 keys are 64 hex characters and ECDSA P-384 keys are 96.

There is a **second, separate proof** that catches people out. Owning the namespace does not prove you own the package the entry points at, so each package type has its own marker:

| Type | Proof |
|---|---|
| npm | `"mcpName": "io.github.user/x"` in `package.json` |
| PyPI | `<!-- mcp-name: io.github.user/x -->` in the README |
| NuGet | the same comment in the README |
| Open Container Initiative image | `LABEL io.modelcontextprotocol.server.name="…"` |
| MCPB | the URL must contain the string `mcp`, and `fileSha256` is required |

For a Python package that marker is an HTML comment in the README, which PyPI renders invisibly and the registry reads off the project page. It is already in [code/05-first-server/README.md](../../code/05-first-server/README.md), directly under the heading, and if it is missing the publish fails with "Registry validation failed for package".

Two things the documentation does not settle. The public package-types page omits Cargo and Quay.io, while the repository's own requirements document lists both, and no ownership-verification method for Cargo is documented anywhere. If you publish a Rust server, expect to find out by trying.

## 5. Publishing with the CLI

![The publish flow as a left-to-right sequence with two gates. The command sequence runs init, validate, login, publish, status. Between login and publish sit two separate ownership gates drawn as checkpoints: namespace ownership, proved by GitHub identity, a DNS TXT record, or a file on your own domain, and package ownership, proved by a marker inside the published package itself. A parallel lower track shows the same flow inside continuous integration, where login github-oidc replaces the interactive device-code login and the workflow needs only the id-token write permission and no stored secret. A note marks where the audience claim comes from.](diagrams/02-publish-flow.svg) *Five commands, two independent ownership proofs, and one of them lives inside your package.*

`mcp-publisher` is a single binary. Install it from a release archive or with `brew install mcp-publisher`. The command surface:

```bash
mcp-publisher init [-y]                      # write a server.json template
mcp-publisher validate [file]                # default ./server.json
mcp-publisher login <method> [...]
mcp-publisher publish [PATH]
mcp-publisher status --status <active|deprecated|deleted> [--message M] <name> [version]
```

The `--help` text reproduced in the official quickstart lists only `init`, `login`, `logout` and `publish`. It omits `validate` and `status`, both of which are fully documented in the repository's own command reference. Run them anyway.

`validate` is the cheap one and the one people skip. Its output on a bad file names the field and whether the problem is schematic or semantic:

```text
$ mcp-publisher validate custom-server.json
Validation failed with 2 issue(s):

1. [error] repository.url (schema)
   '' has invalid format 'uri'

2. [error] name (semantic)
   server name must be in format 'dns-namespace/name'
   Reference: invalid-server-name
```

Login has several methods. Interactive GitHub uses a device code and grants `io.github.{username}/*` and `io.github.{org}/*`. Domain login takes the private key matching the record you published:

```bash
mcp-publisher login github
mcp-publisher login dns  --domain=example.com --private-key=HEX_KEY
mcp-publisher login http --domain=example.com --private-key=HEX_KEY
mcp-publisher login github-oidc
```

Then `mcp-publisher publish`, and a `curl` against `https://registry.modelcontextprotocol.io/v0.1/servers?search=...` to confirm.

**Deprecating your own server** is the `status` command, and it is the only lever you have after the fact:

```bash
mcp-publisher status --status deprecated --message "Please upgrade to 2.0.0" io.github.user/my-server 1.0.0
mcp-publisher status --status deprecated --all-versions --message "Project archived" io.github.user/my-server
```

Metadata is immutable per version. You publish a new version; you never edit an old one.

## 6. Automating it, with no stored secret

The interesting part of the continuous integration story is that there is nothing to store. GitHub Actions can mint an OpenID Connect (OIDC) token for a job, and the registry validates it directly. The workflow needs one permission:

```yaml
permissions:
  id-token: write     # this is what makes github-oidc work
  contents: read
```

and one login line:

```yaml
- run: ./mcp-publisher login github-oidc
```

OIDC grants the **repository owner's** namespace, which is why `io.github.ajkumar-13/*` works from a repository owned by that account with no configuration.

One mechanism worth knowing before it bites you. The audience claim is derived, not configured:

> The CLI derives the OIDC `aud` claim from `--registry` (scheme + host, e.g. `https://registry.modelcontextprotocol.io`) so tokens are bound to the specific deployment they were minted for.

The consequence is that an older `mcp-publisher` fails against a current registry with `invalid audience`, and the fix is to upgrade the publisher rather than to change anything on your side.

The complete workflow for this series lives at [code/05-first-server/.github/workflows/publish.yml](../../code/05-first-server/.github/workflows/publish.yml). It runs on a `v*` tag and does five things in order: run the tests, build and publish the package to PyPI with trusted publishing, stamp the tag's version into both `version` fields of `server.json`, validate that file, and publish. The version stamping matters more than it looks:

```yaml
- name: Stamp the version from the tag
  if: startsWith(github.ref, 'refs/tags/v')
  run: |
    VERSION="${GITHUB_REF#refs/tags/v}"
    jq --arg v "$VERSION" '.version = $v | .packages[0].version = $v' \
      server.json > server.tmp && mv server.tmp server.json
```

Both fields have to move together. Bump only the top-level `version` and the registry advertises a release of a package that does not exist on PyPI yet, and you cannot take either statement back.

Order matters too. Publish the package first and the registry second, because the registry validates that the package exists and carries your ownership marker. Do it the other way round and the publish fails.

## 7. MCPB bundles, for the desktop

The other distribution path is a single file a user double-clicks. It has been renamed once and moved once, and both facts are still visible in the wild.

| When | What happened |
|---|---|
| 2025-07-20 | `@anthropic-ai/dxt` 0.2.6, the final release under the old name |
| 2025-08-21 | The rename commit, `dxt` to `mcpb` |
| 2025-09-11 | `@anthropic-ai/mcpb` 1.0.0 on npm |
| late Nov 2025 | The format, the command line tool, and the reference implementation transfer from Anthropic to the `modelcontextprotocol` organization |
| 2025-12-04 | v2.1.2, still the latest published release |

The compatibility story is precise and asymmetric. **`dxt_version` is still accepted** as an alias for `manifest_version`, across every schema version from 0.1 to 0.4, marked deprecated. **The `.dxt` file extension is not** honored anywhere in the tool; only `.mcpb`. So an old manifest keeps working and an old filename does not.

![The anatomy of a .mcpb file. The upper half shows the ZIP contents: manifest.json at the root, which is the only required file, a server directory holding the entry point, an optional icon.png, and whatever else the server needs. The lower half shows the optional signature block appended after the ZIP content, delimited by an MCPB_SIG_V1 marker, a four-byte little-endian length prefix, a DER-encoded PKCS number seven signature, and an MCPB_SIG_END marker, with a note that the signature is detached so the original ZIP content is unmodified. A side panel lists the manifest's five required fields and flags the unresolved disagreement between sources about what manifest_version should be.](diagrams/03-bundle-anatomy.svg) *A ZIP file with a manifest, plus an optional signature appended after the archive rather than inside it.*

The manifest is `manifest.json` at the bundle root, and it is the only required file. Five fields are required inside it: `name`, `version`, `description`, `author`, and `server`. The `server` object requires `type`, `entry_point`, and `mcp_config`, and `type` is one of `python`, `node`, `binary`, or `uv`.

```json
{
  "manifest_version": "0.3",
  "name": "my-extension",
  "version": "1.0.0",
  "description": "A simple MCP extension",
  "author": { "name": "Extension Author" },
  "server": {
    "type": "node",
    "entry_point": "server/index.js",
    "mcp_config": {
      "command": "node",
      "args": ["${__dirname}/server/index.js"],
      "env": { "API_KEY": "${user_config.api_key}" }
    }
  },
  "user_config": {
    "api_key": {
      "type": "string",
      "title": "API Key",
      "description": "Your API key for authentication",
      "sensitive": true,
      "required": true
    }
  }
}
```

`user_config` is the reason the format exists. It declares the settings the installer should ask for, with types (`string`, `number`, `boolean`, `directory`, `file`), and `sensitive: true` tells the host to mask the input and store it securely. Values arrive back through `${user_config.KEY}` substitution in `args` and `env`, alongside `${__dirname}`, `${HOME}`, `${DOCUMENTS}`, and a handful of others.

The workflow is four commands:

```bash
mcpb init
mcpb pack .
mcpb sign my-extension.mcpb --self-signed
mcpb verify my-extension.mcpb
```

Signing appends rather than embeds. A signed bundle is the original ZIP content, then a marker `MCPB_SIG_V1`, a four-byte little-endian length, a DER-encoded PKCS number seven signature, and a closing `MCPB_SIG_END`. `verify` reports whether a bundle is signed, self-signed, or unsigned, and prints `WARNING: This extension is self-signed` where that applies. What a desktop application does with that information at install time is not documented on any primary page, so do not assume a self-signed bundle will be refused, or accepted.

A `.mcpb` can also be a registry package. `registryType: "mcpb"` is first-class, the `identifier` is a release asset URL that **MUST** contain the string `mcp`, a `fileSha256` is **required**, and hosting is restricted to github.com and gitlab.com releases. The registry does not check that hash; clients do, before installation.

## 8. Three places the sources disagree

This series' rule is that a hedge beats a confident guess. Three disagreements here could not be settled from primary sources, and rather than pick a side, here they are.

**What `mcpb init` writes for `manifest_version`.** Five sources, three answers:

| Source | Value |
|---|---|
| `LATEST_MANIFEST_VERSION` at the published tag v2.1.2 | `0.4` |
| `DEFAULT_MANIFEST_VERSION` on `main` | `0.3` |
| `DEFAULT_MANIFEST_VERSION` at the published tag v2.1.2 | `0.2` |
| `MANIFEST.md` header | `Current version: 0.3` |
| `mcpb-manifest-latest.schema.json` | `const: "0.3"` |

The published command line tool is v2.1.2, so what you get from `npm install -g @anthropic-ai/mcpb` and `mcpb init` today is most likely `0.2`. That was read out of the source at the tag and **not confirmed by running the tool**, so it is a reading rather than a measurement. Validation accepts 0.1 through 0.4 either way, which makes the practical answer easy: **set `manifest_version` explicitly** and never depend on the default.

**Whether `mcp_config` is optional for `server.type: "uv"`.** `MANIFEST.md` says it is optional, with the parenthetical "host manages execution". The v0.4 schema disagrees: `server` requires `type`, `entry_point`, and `mcp_config`, with no conditional on the type, and the repository's own `hello-world-uv` example supplies one. **The schema wins. Treat `mcp_config` as required.** The prose is out of date.

**Whether the bundle tooling is being maintained.** The last published release is v2.1.2 from 2025-12-04. The `main` branch carries commits through April 2026 and none since. So `clean`, `unpack`, `init --manifest-version`, and `pack --manifest` are real, and they are real *on `main`*, not on npm. `CLI.md` documents none of them. Budget for reading the source.

## 9. What the stateless revision changed here, which is nothing

It is worth saying explicitly, because it is the one part of this series where a reader might reasonably expect breakage and there is none.

Neither the registry nor the bundle format is affected by revision `2026-07-28`. The evidence is negative and checkable: searching `server.schema.json` for `protocolVersion`, `2026-07-28`, or `server/discover` returns zero matches, the registry's OpenAPI document contains no protocol version field, and no version of the MCPB manifest schema from 0.1 to 0.4 has one either.

The reason is structural. `server.json` and `manifest.json` are **static installation descriptors**, not wire artifacts. They answer "what do I run, and where do I connect", never "how do I speak MCP". The only protocol-adjacent things `server.json` carries are transport type bindings and a `version` field that the schema itself comments as "Equivalent of `Implementation.version`", which is the server's own release version rather than a protocol revision.

A server migrating from the 2025-11-25 handshake to the stateless model therefore publishes an identical `server.json`, modulo a version bump. The change is entirely on the client side, after the URL has been resolved.

There is exactly one counterexample, and it is instructive. `MANIFEST.md` shows a vendor-namespaced `_meta` example that pre-caches an `initialize` response, including a `protocolVersion` of `2025-06-18`. Under `2026-07-28` there is no `initialize` method, so that cached answer is for a request that can no longer be made. It is vendor metadata rather than core MCPB, but it is a clean illustration of why a protocol revision leaking into packaging metadata is a smell.

---

## Common pitfalls

- **Treating a registry listing as a safety signal.** The moderation policy will not remove low-quality servers, duplicates, adult content, or servers with known security vulnerabilities. A listing proves namespace ownership and nothing else.
- **Publishing something you might need to retract.** There is no unpublish. You can set a status, and even a `deleted` server's metadata stays queryable through the API.
- **A description longer than 100 characters.** It is a hard schema limit, it fails at validation rather than at publish, and it is the single most common first failure.
- **Forgetting the second ownership proof.** Namespace ownership and package ownership are separate. A Python package needs `<!-- mcp-name: … -->` in the README that PyPI renders, or the publish is rejected.
- **Bumping one version and not the other.** `version` and `packages[].version` have to move together, or the registry advertises a release that does not exist on the package registry yet.
- **Publishing to the registry before publishing the package.** The registry checks that the artifact exists and carries the marker. Package first, registry second.
- **Relying on `mcpb init`'s default `manifest_version`.** The published tool, the repository's `main` branch, and the specification prose disagree about what it should be. Write the field yourself.
- **Shipping a `.dxt` filename.** The `dxt_version` manifest key is still accepted as an alias; the `.dxt` extension is not honored anywhere in the tool.

---

## Further reading

- MCP Registry documentation: about, quickstart, authentication, package types, remote servers, GitHub Actions, versioning, moderation policy, terms of service.
- `server.json` schema, version `2025-12-11`, and the registry OpenAPI document.
- The MCP Registry repository, for `docs/reference/cli/commands.md` and the official-registry requirements.
- The MCPB repository, for `MANIFEST.md`, `CLI.md`, `src/schemas/`, and `examples/`.
- Anthropic, *"MCP Bundles"* installation documentation, for how a desktop application installs a `.mcpb`.

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 23 — Project 4 · One server, every client](../23-multi-client/index.md)**: once it is installable, the next question is whether it behaves the same in every host that installs it.
- **[Post 21 — Deploying to production: containers, scaling, and observability](../21-deploying/index.md)**: the remote-server route in the table above, from container to load balancer.
