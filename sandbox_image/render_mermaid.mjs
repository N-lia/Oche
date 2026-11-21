#!/usr/bin/env node
/**
 * render-mermaid: Mermaid text -> SVG (and optional PNG), using Pele.
 *
 * Pele is a JavaScript library, not a command-line program. Running
 * `node .../pele/dist/pele.js` directly does nothing: it only defines the
 * exported `render()` function, so it exits 0 and writes no file. This wrapper
 * is the supported interface inside the OYI sandbox.
 *
 * Usage:
 *   render-mermaid INPUT.mmd -o OUTPUT.svg
 *   render-mermaid INPUT.mmd -o OUTPUT.svg --png OUTPUT.png
 *   cat input.mmd | render-mermaid -o output.svg
 *   render-mermaid INPUT.mmd            # SVG on stdout
 *
 * Exit codes:
 *   0  SVG (and PNG, when requested) written
 *   2  bad arguments, empty input, or invalid Mermaid
 *   1  unexpected I/O or rasterization error
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { render } from "pele";
import sharp from "sharp";

const USAGE = `Usage: render-mermaid [INPUT|-] [-o OUTPUT|-] [--png OUTPUT.png]

Render Mermaid diagram text to SVG with Pele. Optionally rasterize a PNG for
chat delivery (Telegram photos cannot display SVG).

  INPUT            Mermaid source file (default: read stdin; '-' means stdin)
  -i, --input      Same as INPUT
  -o, --output     SVG output file (default: stdout; '-' means stdout)
      --png        Also write a white-background PNG here
  -h, --help       Show this help

Examples:
  render-mermaid diagram.mmd -o /tmp/diagram.svg
  render-mermaid diagram.mmd -o /tmp/diagram.svg --png /tmp/diagram.png
  printf 'flowchart LR\\n  A[text] --> B[SVG]\\n' | render-mermaid -o /tmp/d.svg
`;

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

const CSS_VAR = /var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)/g;

/**
 * Resolve Pele's CSS variables to concrete values for rasterization.
 *
 * Pele deliberately emits `fill="var(--_s)"` and a style block of `--_*`
 * definitions so a browser can re-theme without re-rendering. librsvg (used by
 * sharp) does not resolve those variables, so without this step every node is
 * filled black and the text disappears into it. The SVG written to disk keeps
 * the variables; only the PNG gets the resolved copy.
 */
function resolveCssVariables(svg) {
  const styleMatch = svg.match(/style="([^"]*)"/);
  if (!styleMatch) return svg;

  const definitions = new Map();
  for (const declaration of styleMatch[1].split(";")) {
    const colon = declaration.indexOf(":");
    if (colon < 0) continue;
    const name = declaration.slice(0, colon).trim();
    if (name.startsWith("--")) {
      definitions.set(name, declaration.slice(colon + 1).trim());
    }
  }
  if (definitions.size === 0) return svg;

  function resolveValue(value, seen = new Set()) {
    return value.replace(CSS_VAR, (match, name, fallback) => {
      if (definitions.has(name) && !seen.has(name)) {
        return resolveValue(definitions.get(name), new Set([...seen, name]));
      }
      return fallback !== undefined ? fallback.trim() : match;
    });
  }

  return svg.replace(CSS_VAR, (match, name, fallback) => {
    if (definitions.has(name)) return resolveValue(definitions.get(name));
    return fallback !== undefined ? fallback.trim() : match;
  });
}


function fail(message, code = 2) {
  process.stderr.write(`render-mermaid: ${message}\n`);
  process.exit(code);
}

function parseArgs(argv) {
  let input = null;
  let output = null;
  let png = null;

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "-h" || arg === "--help") {
      process.stdout.write(USAGE);
      process.exit(0);
    } else if (arg === "-i" || arg === "--input") {
      input = argv[++i];
      if (input === undefined) fail(`${arg} needs a path`);
    } else if (arg.startsWith("--input=")) {
      input = arg.slice("--input=".length);
    } else if (arg === "-o" || arg === "--output") {
      output = argv[++i];
      if (output === undefined) fail(`${arg} needs a path`);
    } else if (arg.startsWith("--output=")) {
      output = arg.slice("--output=".length);
    } else if (arg === "--png") {
      png = argv[++i];
      if (png === undefined) fail("--png needs a path");
    } else if (arg.startsWith("--png=")) {
      png = arg.slice("--png=".length);
    } else if (arg === "-") {
      input = "-";
    } else if (arg.startsWith("-")) {
      fail(`unknown option ${arg}\n\n${USAGE}`);
    } else if (input === null) {
      input = arg;
    } else {
      fail(`unexpected argument ${arg}\n\n${USAGE}`);
    }
  }

  return { input, output, png };
}

function readSource(input) {
  if (input === null || input === "-") {
    try {
      return readFileSync(0, "utf8");
    } catch (error) {
      fail(`could not read stdin: ${error.message}`, 1);
    }
  }

  try {
    return readFileSync(input, "utf8");
  } catch (error) {
    fail(`could not read ${input}: ${error.message}`, 1);
  }
}

async function writePng(svg, pngPath) {
  const path = resolve(pngPath);
  try {
    mkdirSync(dirname(path), { recursive: true });
    await sharp(Buffer.from(resolveCssVariables(svg)), { density: 144 })
      .resize({ width: 2000, height: 2000, fit: "inside", withoutEnlargement: true })
      .flatten({ background: "#ffffff" })
      .png({ compressionLevel: 9 })
      .toFile(path);
  } catch (error) {
    fail(`could not write ${path}: ${error.message}`, 1);
  }

  let header;
  try {
    header = readFileSync(path).subarray(0, PNG_SIGNATURE.length);
  } catch (error) {
    fail(`could not read back ${path}: ${error.message}`, 1);
  }
  if (!header.equals(PNG_SIGNATURE)) {
    fail(`${path} is not a PNG`, 1);
  }

  return path;
}

async function main() {
  const { input, output, png } = parseArgs(process.argv.slice(2));
  const source = readSource(input);

  if (!source.trim()) {
    fail("no Mermaid source (pass a file or pipe text on stdin)");
  }

  let result;
  try {
    result = render(source);
  } catch (error) {
    fail(`invalid Mermaid: ${error?.message ?? String(error)}`);
  }

  const svg = result?.svg;
  if (typeof svg !== "string" || !svg.trimStart().startsWith("<svg")) {
    fail("Pele returned no SVG");
  }

  if (output === null || output === "-") {
    process.stdout.write(svg.endsWith("\n") ? svg : `${svg}\n`);
  } else {
    const path = resolve(output);
    try {
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, svg, "utf8");
    } catch (error) {
      fail(`could not write ${path}: ${error.message}`, 1);
    }

    const viewBox = svg.match(/viewBox="([^"]+)"/)?.[1] ?? "unknown";
    process.stderr.write(
      `render-mermaid: wrote ${path} (${svg.length} bytes, viewBox ${viewBox})\n`,
    );
  }

  if (png !== null && png !== "-") {
    const pngPath = await writePng(svg, png);
    const bytes = readFileSync(pngPath).length;
    process.stderr.write(`render-mermaid: wrote ${pngPath} (${bytes} bytes, PNG)\n`);
  }
}

await main();
