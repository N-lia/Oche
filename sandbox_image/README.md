# `oyi-sandbox` — the image the OYI agent's shell runs in

The sandbox is a real Linux environment, but it is not the webapp container.
It needs the toolchains the agent actually uses:

| Tool | Why |
| --- | --- |
| Python 3.12 | deepagents' file tools (`read_file`, `write_file`, `grep`, …) execute Python snippets |
| Node 20 | [Pele](https://pele.run) requires Node >= 20 |
| `pele@1.10.1` | renders Mermaid text to SVG |
| `sharp@0.33.5` | rasterizes the SVG to PNG for Telegram photos |
| `fontconfig` + DejaVu | sharp/librsvg needs a real font for diagram labels |
| `render-mermaid` | the supported command; Pele itself is a **library**, not a CLI |

## Why the old call failed

This is a no-op that exits 0 and writes nothing:

```sh
node /usr/local/lib/node_modules/pele/dist/pele.js render mermaid \
  --input-file /tmp/input.mmd --output /tmp/diagram.svg
```

`pele.js` only defines exports. There is no `render` subcommand, so there is no
error and no output file. The same is true of `npx pele`: the package has no
binary.

## Correct use

```sh
# from a file
render-mermaid /tmp/input.mmd -o /tmp/diagram.svg

# from stdin
printf 'flowchart LR\n  A[Mermaid] --> B[SVG]\n' | render-mermaid -o /tmp/diagram.svg

# also rasterize a PNG for chat delivery
render-mermaid /tmp/input.mmd -o /tmp/diagram.svg --png /tmp/diagram.png
```

It writes an SVG and prints the byte count and viewBox to stderr:

```
render-mermaid: wrote /tmp/diagram.svg (2916 bytes, viewBox 0 0 223 324)
render-mermaid: wrote /tmp/diagram.png (17427 bytes, PNG)
```

Invalid Mermaid exits non-zero with the parser error. Verify the result:

```sh
test -s /tmp/diagram.svg && head -c 100 /tmp/diagram.svg
```

### Why the PNG needs a CSS pass first

Pele emits `fill="var(--_s)"` and a style block of `--_*` definitions so a
browser can re-theme without re-rendering. librsvg (inside sharp) does not
resolve CSS variables, so a naive rasterization fills every node black and the
text disappears. `render-mermaid` resolves those variables to concrete colors
for the PNG only. The SVG on disk keeps its variables and stays themeable in a
browser.

## Build

```sh
export NEBIUS_API_KEY=...
export NEBIUS_PROJECT_ID=aiproject-...
contree build sandbox_image --tag oyi-sandbox:latest
```

Then point the app at it:

```sh
OYI_SANDBOX_IMAGE=oyi-sandbox:latest
```

The build runs a smoke test: it renders a small diagram, checks the SVG starts
with `<svg>`, checks the PNG starts with the PNG signature, checks that a
DejaVu font is available, checks Node >= 20, and checks Python >= 3.11. A
broken image cannot be tagged.

## Using Pele directly

If a task needs the API rather than the command, import it from the fixed
location:

```js
import { render } from "/opt/oyi/node_modules/pele/dist/pele.js";

const { svg } = render("flowchart LR\n  A-->B");
```

`import "pele"` from an arbitrary directory will not resolve; ESM ignores
`NODE_PATH`. Use the absolute path or the `render-mermaid` wrapper.
