# Diagram style guide

Every diagram in this series speaks the same visual language. Departures look out of place;
adherence makes the whole repository feel like one book. The source of truth for the tokens is
[assets/diagrams/style/tokens.css](../assets/diagrams/style/tokens.css).

The palette is shared with the sibling "from scratch" series, so a reader who has seen one
recognises the other. Only the prefix differs: `--mcp-*` here, `--mi-*` there.

---

## Palette (the `--mcp-*` tokens)

| Token | Light | Meaning in this series |
|-------|-------|------------------------|
| `--mcp-bg` | `#FAFAF7` | page background |
| `--mcp-surface` | `#FFFFFF` | cards, boxes |
| `--mcp-ink` | `#1A1A1A` | text, primary strokes |
| `--mcp-ink-muted` | `#5C5C5C` | secondary text |
| `--mcp-ink-subtle` | `#9A9A9A` | tertiary text |
| `--mcp-border` | `#D9D9D4` | hairline borders |
| `--mcp-primary` | `#5B7FBF` | **the client side** — hosts, clients, the requesting party |
| `--mcp-accent` | `#D98E5F` | **the server side** — servers, tools, the responding party |
| `--mcp-success` | `#5C9E78` | an allowed path, a passing check, the "after" state |
| `--mcp-warn` | `#B8895A` | caution; a deprecated-but-present feature |
| `--mcp-alert` | `#C66B5E` | blocked, error, removed, the "before" state |
| `--mcp-on-fill` | `#FFFDF9` | knockout text sitting on a saturated fill |

Each token has a dark-mode value in the `@media (prefers-color-scheme: dark)` block. Every SVG
**inlines** the `:root` + `@media` block in its own `<style><![CDATA[ … ]]></style>`. There is
no external stylesheet — diagrams must render standalone, as `<img>`, and inside a static site.

**Semantic convention, used across the whole series.** Client-side things are blue, server-side
things are terracotta, and the direction of a request runs blue to terracotta. A reader should be
able to tell which side of the protocol boundary a box is on without reading its label.

Never use `#FFFFFF` directly for knockout text — use `var(--mcp-on-fill)`, which flips in dark
mode. A hardcoded white is a checker warning.

## Typography

- **Labels:** Inter — 12–14 px body, 16–20 px titles. (`.mcp-label`, `.mcp-small`, `.mcp-title`)
- **Code, JSON, method names, headers:** JetBrains Mono, 13 px. (`.mcp-mono`)
- All text is `<text>`; never images of text. Letter-spacing 0, line-height ~1.3.
- Protocol method names (`tools/call`, `server/discover`) are always mono, never sentence case.

## Strokes

- Primary structural lines: **1.5 px**, `var(--mcp-ink)`.
- Secondary lines (annotations, leaders): **1.0 px**, `var(--mcp-ink-muted)`.
- Grid lines: **0.75 px**, `var(--mcp-grid)`.

Those three widths are the entire vocabulary. Anything else is a checker warning.

## Fills

- Boxes and nodes: `var(--mcp-surface)` interior, `var(--mcp-ink)` 1.5 stroke.
- Highlights follow the semantic palette above — **never colour alone**; pair with shape,
  position, or text so the diagram survives greyscale and colour-blindness.

## No emoji, ever

Emoji glyphs rasterise differently on every platform and fail the checker. Draw the shape
instead: a check becomes a `<path>` tick, a cross becomes two strokes, a warning becomes a
triangle. The previous generation of diagrams in this repo leaned on 👤🧠⚡✅ — none of that
survives.

## Layout

- `viewBox` always set. **No `width`/`height` on the root `<svg>`** — the checker fails those,
  and they break responsive embedding.
- Padding: ≥ 24 px around bounding content.
- Aspect ratios: **16:9** hero (960×540), **4:3** inline (800×600), **1:1** icon.
- Wide sequence diagrams may use 960×720; keep the width at 960 so posts render evenly.

## Accessibility

Every diagram carries `role="img" aria-labelledby="t d"` plus a `<title id="t">` and a
`<desc id="d">`. The `<desc>` is a real description — two or three sentences that would let
someone who cannot see the image follow the argument the diagram is making. It is not a caption
and not a repeat of the title.

## Filenames

Post-local diagrams: `posts/NN-slug/diagrams/NN-short-name.svg`, numbered from `01` within the
post. Cross-post exports: `assets/diagrams/exports/short-name.svg`. The hero named in a post's
`frontmatter.yaml` is that post's `diagrams/01-…svg`.

---

## Boilerplate

Copy this skeleton for every new diagram. It already satisfies every mechanical check.

```svg
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 540" role="img" aria-labelledby="t d">
  <title id="t">Short title, sentence case</title>
  <desc id="d">Two or three sentences describing what the diagram shows and what it argues,
  written for someone who cannot see it.</desc>

  <style><![CDATA[
    :root{--mcp-bg:#FAFAF7;--mcp-surface:#FFFFFF;--mcp-ink:#1A1A1A;--mcp-ink-muted:#5C5C5C;--mcp-ink-subtle:#9A9A9A;--mcp-border:#D9D9D4;--mcp-primary:#5B7FBF;--mcp-accent:#D98E5F;--mcp-success:#5C9E78;--mcp-warn:#B8895A;--mcp-alert:#C66B5E;--mcp-neutral-1:#EAEAE4;--mcp-neutral-2:#CFCFC8;--mcp-on-fill:#FFFDF9;}
    @media (prefers-color-scheme:dark){:root{--mcp-bg:#0E0F12;--mcp-surface:#16181C;--mcp-ink:#F2F2EE;--mcp-ink-muted:#B4B4AE;--mcp-ink-subtle:#6E6E68;--mcp-border:#2A2D33;--mcp-primary:#8BA8E0;--mcp-accent:#E8B088;--mcp-success:#7FBF9B;--mcp-warn:#D4B58A;--mcp-alert:#D88880;--mcp-neutral-1:#1F2229;--mcp-neutral-2:#2C3038;--mcp-on-fill:#14161A;}}
    .bg{fill:var(--mcp-bg);}
    .title{font-family:Inter,system-ui,sans-serif;font-weight:600;font-size:20px;fill:var(--mcp-ink);}
    .label{font-family:Inter,system-ui,sans-serif;font-weight:600;font-size:14px;fill:var(--mcp-ink);}
    .small{font-family:Inter,system-ui,sans-serif;font-weight:400;font-size:12px;fill:var(--mcp-ink-muted);}
    .mono{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-weight:400;font-size:13px;fill:var(--mcp-ink);}
    .box{fill:var(--mcp-surface);stroke:var(--mcp-ink);stroke-width:1.5;}
    .chip{fill:var(--mcp-surface);stroke:var(--mcp-border);stroke-width:1;}
    .client{fill:var(--mcp-primary);}
    .server{fill:var(--mcp-accent);}
    .ok{fill:var(--mcp-success);}
    .bad{fill:var(--mcp-alert);}
    .onfill{font-family:Inter,system-ui,sans-serif;font-weight:600;font-size:13px;fill:var(--mcp-on-fill);}
    .flow{stroke:var(--mcp-ink);stroke-width:1.5;fill:none;}
    .flow-thin{stroke:var(--mcp-ink-muted);stroke-width:1;fill:none;}
    .lifeline{stroke:var(--mcp-ink-muted);stroke-width:1;stroke-dasharray:4 4;fill:none;}
  ]]></style>

  <defs>
    <marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--mcp-ink)"/>
    </marker>
  </defs>

  <rect class="bg" width="960" height="540"/>
  <!-- content -->
</svg>
```

---

## Checklist before committing a diagram

- [ ] Inlines the `:root` variables and the `@media` dark block.
- [ ] Uses only the `--mcp-*` semantic colours; no raw hex in the body; no `#FFFFFF`.
- [ ] Client side blue, server side terracotta.
- [ ] Inter for labels; JetBrains Mono for method names, JSON, and headers.
- [ ] Strokes 1.5 / 1.0 / 0.75 only.
- [ ] `viewBox` set; no `width`/`height` on the root; ≥24 px padding.
- [ ] Has `<title>` and `<desc>`, and `role="img" aria-labelledby`.
- [ ] No emoji glyphs anywhere.
- [ ] Information is never encoded in colour alone.
- [ ] Every protocol method name and header spelled exactly as the 2026-07-28 spec spells it.
- [ ] Renders correctly in both light and dark mode.
