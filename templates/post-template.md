# NN · Title

> **TL;DR.** Two to four sentences. The first names the post's claim; the rest name
> what the post will deliver. No metadata in here.
>
> **After reading this you will be able to:**
> - One concrete capability the reader gains. Verb first.
> - A second concrete capability.
> - A third concrete capability.

![Alt text that describes the diagram, not the post.](diagrams/01-hero-name.svg)
*Caption for the hero diagram, one short sentence.*

---

## 1. The motivation

Open with the symptom, question, or concrete failure this post resolves. Picture first,
then intuition. Concrete, not abstract. Never open with a definition.

## 2. The mechanism

Explain how it works in plain language. Diagram before wire format, wire format before
code. Show the actual JSON before the SDK call that produces it, so the reader can debug
without the SDK.

## 3. The code

Runnable code against `mcp` 2.x and protocol revision 2026-07-28. Every snippet either
runs as shown or names the file it belongs to in `code/`. No pseudo-code presented as
real code, no invented API surface.

## 4. Running it

What the reader types, and what they see. Real output only — never a fabricated
transcript. If output cannot be reproduced here, say so and show the shape instead.

## 5. The variations

Two or three variants, each with the situation where it is the right choice.

## 6. When *not* to use it

A short, honest section on the edge of applicability.

---

## Common pitfalls

- Bullet 1: a mistake the reader will actually make.
- Bullet 2: ditto.
- (4–7 bullets total.)

---

## Further reading

- Spec, *"Page title"*, revision 2026-07-28.
- Author, *"Title"* (year).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post NN — Title](../NN-slug/index.md)**: one-line "why next".
- **[Post NN — Title](../NN-slug/index.md)**: sideways link.

---

## House rules this template encodes

Delete this section in a real post; it is here as the checklist.

1. **Frontmatter lives only in `frontmatter.yaml`.** No `---` block in `index.md`, and no
   restated "Reading time" line in the body — both are hard checker failures.
2. **`> **TL;DR.**` is mandatory**, four sentences or fewer.
3. **`## Common pitfalls` is mandatory.**
4. **Ten em-dashes per post, maximum.** Prefer a comma, a colon, or a full stop.
5. **No emoji** in prose or diagrams.
6. **Second person is fine** ("you"), hype is not. No ALL-CAPS shouting, no "the secret
   sauce", no "let's go".
7. **Every acronym expanded on first use in every post** — readers arrive mid-series.
8. **Every relative link must resolve**, and must use forward slashes.
9. **No invented numbers and no invented output.** Cite or reproduce.
10. **One protocol revision: 2026-07-28.** When something differs from an older revision
    that readers may have seen, mark it with a short "Changed in 2026-07-28" note rather
    than teaching both.
