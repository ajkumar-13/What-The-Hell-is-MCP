# One page of MCP

One scene, in two forms:

| File | What it is |
|---|---|
| `one-page-of-mcp.excalidraw` | An Excalidraw scene. Open it at [excalidraw.com](https://excalidraw.com) or in the VS Code Excalidraw extension and edit it directly. |
| `one-page-of-mcp.svg` | The same scene rendered to a self-contained SVG with [roughjs](https://roughjs.com), themed with the repo's `--mcp-*` tokens so it works in light and dark mode. |

The whole protocol on one canvas: eight panels covering the three roles, the three primitives
and who triggers each, the shape of a self-describing request, every method including what
this revision removed, the MRTR loop that replaced the server-to-client channel, the split
between protocol errors and tool execution errors, the two transports, and a security
checklist. It prints at A2.

There is a second, typeset version of the same sheet in the working tree that sets Inter and
JetBrains Mono. It is not published here. This drawn one is the better fit for a talk, a
whiteboard session, or a workshop where you want to pull a panel out and scribble on it, and
its panel geometry is copied from the typeset sheet coordinate for coordinate so the two can
be held side by side. Three things differ:

- **The type is smaller.** Excalifont runs wider than Inter at the same nominal size, so the
  body sizes come down to hold the same column widths.
- **Text on a colored fill uses `--mcp-on-accent`,** which is dark ink in both themes. The
  printed sheet uses `--mcp-on-fill` for the client and server chips, which measures 2.59:1
  against `--mcp-primary`; on-accent measures 6.62 in light mode and 9.48 in dark.
- **The strike-throughs over the removed methods are derived, not hand-placed.** A literal
  end coordinate that suits Inter overshoots Excalifont badly, so the strike length is
  computed from the text it crosses.

## Regenerating

The generator lives in the working tree rather than in this repository, alongside the ones
that draw the per-post companions:

```bash
cd assets/diagrams/excalidraw-generator
npm install
npm run build:poster
```

Every element carries a seed derived from its index, so a rebuild is byte-identical. Nothing
here depends on a random number, which is what keeps a regeneration from showing up as a diff.

Coordinates in `poster.js` are the printed sheet's own, which means they are text *baselines*.
`tb()` converts a baseline into the top-left origin an Excalidraw text element wants, so every
number in the generator can be checked against the source SVG line by line.
