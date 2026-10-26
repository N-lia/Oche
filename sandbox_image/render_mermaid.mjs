#!/usr/bin/env node
/**
 * render-mermaid: Mermaid text -> SVG, using Pele.
 *
 * Pele is a JavaScript library, not a command-line program. Running
 * `node .../pele/dist/pele.js` directly does nothing: it only defines the
 * exported `render()` function, so it exits 0 and writes no file. This wrapper
 * is the supported interface inside the OYI sandbox.
 *
 * Usage:
 *   render-mermaid INPUT.mmd -o OUTPUT.svg
 *   cat input.mmd | render-mermaid -o output.svg
 *   render-mermaid INPUT.mmd            # SVG on stdout
 *
 * Exit codes:
 *   0  SVG written
 *   2  bad arguments, empty input, or invalid Mermaid
 *   1  unexpected I/O error
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { render } from "pele";

const USAGE = `Usage: render-mermaid [INPUT|-] [-o OUTPUT|-]

Render Mermaid diagram text to SVG with Pele.

  INPUT            Mermaid source file (default: read stdin; '-' means stdin)
  -i, --input      Same as INPUT
  -o, --output     SVG output file (default: stdout; '-' means stdout)
  -h, --help       Show this help

Examples:
  render-mermaid diagram.mmd -o /tmp/diagram.svg
  printf 'flowchart LR\\n  A[text] --> B[SVG]\\n' | render-mermaid -o /tmp/d.svg
`;

function fail(message, code = 2) {
  process.stderr.write(`render-mermaid: ${message}\n`);
  process.exit(code);
}

function parseArgs(argv) {
  let input = null;
  let output = null;

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

  return { input, output };
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

function main() {
  const { input, output } = parseArgs(process.argv.slice(2));
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
    return;
  }

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

main();
