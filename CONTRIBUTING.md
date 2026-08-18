# Contributing

Thank you for helping improve this series. The bar is "a developer who has never heard of
MCP could read this and never feel lost." Small fixes and large ones are both welcome.

## Quick fixes (typos, broken links, clarity, spec drift)

Open a pull request directly. No issue needed. Spec-drift fixes are especially welcome:
this series tracks a protocol revision that is young, and pages move.

Run the quality checker before submitting if you can:

```bash
python <path-to>/blog-quality/checks.py --root .
```

Exit code 0 means clean. Errors block a merge; warnings are advisory.

The checker is a separate tool and is not vendored here, so you may not have it. You do not
need it: every rule it fails a build on is written out below, and a change that respects
them will pass.

## Larger changes (new posts, new diagrams, code companions)

Open an issue first so we can agree on scope. A new post starts by settling its outline
and its worked example in that issue, not by writing prose.

## Repository layout

Every post is a directory:

```
posts/NN-slug/
├── index.md            # the body; never contains a --- frontmatter block
├── frontmatter.yaml    # slug, title, date, tags, hero, reading_time, part
├── diagrams/           # post-local SVGs, numbered from 01
└── snippets/           # short code shown inline (optional)
```

`reading_time` is derived, not guessed. Prose words divided by 250, plus fenced code lines
divided by 25, rounded up, with diagram alt text excluded. Recompute it when a post's length
changes materially, so the numbers stay comparable across the series rather than drifting into
whatever felt right on the day.

`NN` is stable and the slug never changes after publishing, because inbound links break.
Three more structure rules are hard checker errors: `slug` must equal the directory name,
`hero` must resolve to a file inside the post directory, and `index.md` must never restate
the reading time — that field lives only in `frontmatter.yaml`. Post numbers must also run
contiguously from 01, so a new post is appended at the end of the series. Inserting one
mid-series means renumbering directories, which is a decision to agree on in the issue first.

## Writing style

- **Audience is a working developer with no protocol background.** Assume Python, assume
  nothing else.
- **Order is fixed: picture, intuition, wire format, code.** Never lead with a JSON blob
  and never lead with an SDK call.
- Voice: neutral and warm, second person welcome. No hype, no ALL-CAPS, no "the secret
  sauce", no emoji.
- Sentences short. Paragraphs two to four sentences.
- Expand every acronym on first use **in every post**; readers arrive from search engines.
- Use the vocabulary in [notation_guide.md](notation_guide.md) exactly. Host, client, and
  server in particular are not interchangeable.
- **Every post opens with a TL;DR blockquote**, written `> **TL;DR.**`, four sentences or
  fewer. A post without one fails the checker.
- **Every post has a `## Common pitfalls` section**, four to seven bullets, near the end.
  A post without one fails the checker.
- **At most ten em-dashes per post.** The checker enforces this. A comma or a full stop is
  almost always better.
- **Every relative link must resolve, forward slashes only.** A backslash path is an error,
  not a warning: it breaks on GitHub, and this repository is authored on Windows where a
  pasted path is the natural mistake. A cross-reference that reads `[Part 14 ...]` must
  point at `../14-.../`; a number that disagrees with its target is also an error.
- **No invented numbers and no invented output.** Every empirical claim cites a primary
  source in [REFERENCES.md](REFERENCES.md), or is reproducible from [code/](code/).

## Protocol accuracy

This series targets protocol revision **2026-07-28** and only that revision.

- Every method name, field name, and header must be spelled exactly as the specification
  spells it.
- Do not teach `initialize`, sessions, `ping`, `resources/subscribe`, sampling, or roots as
  live features. They are removed or deprecated. Mention them only as "you will meet this
  in older code".
- Claims about the protocol carry a link to the specification page that supports them.
- Where the specification is genuinely ambiguous, say so in the post. A hedge is better
  than a confident guess.

## Code rules

- Target the Python SDK version pinned in each project's `pyproject.toml`. Pin, never float.
- Every Python project under [code/](code/) must import cleanly and pass its tests.
  [code/21-deploy/](code/21-deploy/) is the exception: deployment configuration only, with no
  package, no `pyproject.toml`, and no tests.
- Every number printed in a post must be reproducible. The measured values for
  [code/05-first-server/](code/05-first-server/) come from a capture script that drives the
  server through an in-memory client; every other project's figures come from its own test
  suite. Name the command that produces them, and if a post and its source disagree, the
  post is wrong.
- No secrets, no personal paths, no usernames in committed code, configuration, or images.

## Diagrams

- **The figure a post embeds is generated from a scene file**, not typed by hand into the
  SVG. Every element carries a seed derived from its index, so a rebuild is byte-identical
  and a regeneration never shows up as a diff. Nothing depends on a random number. No
  Mermaid, and no screenshots of text.
- The editable scene lives beside the figure in `posts/NN-slug/diagrams/excalidraw/` and is
  not published; the rendered SVG one level up is.
- Client side blue, server side terracotta.
- `viewBox` only; no `width` or `height` on the root element.
- `<title>` and `<desc>` are mandatory, and the `<desc>` is a real description.
- An inlined `prefers-color-scheme` block is mandatory; check both modes before committing.
- **No emoji.** Draw the shape.

## Screenshots

Screenshots of host interfaces are allowed where the interface is the subject. Scrub them
first: no usernames, no home-directory paths, no personal conversation content, no account
names. A screenshot that needs redaction boxes usually wants to be a diagram instead.

## License

By contributing you agree your prose is released under CC-BY 4.0 and your code under MIT.
