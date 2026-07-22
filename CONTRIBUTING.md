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

## Larger changes (new posts, new diagrams, code companions)

Open an issue first so we can agree on scope. Every post is specified in
[PLAN.md](PLAN.md) section 6; a new post starts by adding or amending its spec there,
not by writing prose.

## Repository layout

Every post is a directory:

```
posts/NN-slug/
├── index.md            # the body; never contains a --- frontmatter block
├── frontmatter.yaml    # slug, title, date, tags, hero, reading_time, part
├── diagrams/           # post-local SVGs, numbered from 01
└── snippets/           # short code shown inline (optional)
```

`NN` is stable and the slug never changes after publishing, because inbound links break.

## Writing style (short version; full guide in [PLAN.md](PLAN.md) section 7)

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
- **At most ten em-dashes per post.** The checker enforces this. A comma or a full stop is
  almost always better.
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
- Every project under [code/](code/) must import cleanly and pass its tests.
- Every number printed in a post must be reproducible from a committed file.
- No secrets, no personal paths, no usernames in committed code, configuration, or images.

## Diagrams

- Follow [templates/diagram-style-guide.md](templates/diagram-style-guide.md) exactly.
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
