#!/usr/bin/env python3
"""Live test: the OYI sandbox image renders Mermaid with Pele.

This test talks to Nebius Sandboxes, so it is skipped unless explicitly
enabled:

    NEBIUS_API_KEY=... NEBIUS_PROJECT_ID=... OYI_LIVE_SANDBOX=1 \
        .venv/bin/python test_sandbox_image.py

It checks three things that the old image failed:
  1. Node >= 20 and Python >= 3.11 are both present
  2. render-mermaid produces a real SVG from a file and from stdin
  3. invalid Mermaid exits non-zero and creates no file
  4. deepagents' ContreeSandbox can still write/read files and execute code
"""

from __future__ import annotations

import asyncio
import os
import textwrap

from sandbox_image_contract import image_ref

LIVE = os.getenv("OYI_LIVE_SANDBOX", "0").strip() in {"1", "true", "yes", "on"}
IMAGE = image_ref()

CHECK_SCRIPT = textwrap.dedent(
    r"""
    set -e
    node -e 'if (Number(process.versions.node.split(".")[0]) < 20) process.exit(1)'
    python3 -c 'import sys; assert sys.version_info >= (3, 11)'
    test -d /workspace
    test -d /home/agent
    echo "node=$(node -v) python=$(python3 -V 2>&1)"

    printf 'flowchart TD\n  A[Mermaid text] --> B{Flowchart?}\n  B -- Yes --> C[Pele]\n  B -- No --> D[Mermaid]\n' \
      > /tmp/input.mmd
    render-mermaid /tmp/input.mmd -o /tmp/diagram.svg --png /tmp/diagram.png
    test -s /tmp/diagram.svg
    grep -q '<svg' /tmp/diagram.svg
    grep -q 'pele-flowchart' /tmp/diagram.svg
    python3 -c 'import pathlib; d=pathlib.Path("/tmp/diagram.png").read_bytes(); assert d[:8] == b"\x89PNG\r\n\x1a\n", d[:8]; assert len(d) > 2000, len(d)'
    echo "file_render=ok bytes=$(wc -c < /tmp/diagram.svg) png=$(wc -c < /tmp/diagram.png)"

    printf 'flowchart LR\n  X[stdin] --> Y[SVG]\n' | render-mermaid -o /tmp/stdin.svg --png /tmp/stdin.png
    test -s /tmp/stdin.svg
    grep -q '<svg' /tmp/stdin.svg
    python3 -c 'import pathlib; d=pathlib.Path("/tmp/stdin.png").read_bytes(); assert d[:8] == b"\x89PNG\r\n\x1a\n"'
    echo "stdin_render=ok"

    if printf 'flowchart TD\n  A -->\n' | render-mermaid -o /tmp/bad.svg --png /tmp/bad.png 2>/tmp/bad.err; then
      echo "invalid_mermaid=unexpected_success"
      exit 9
    fi
    test ! -s /tmp/bad.svg
    test ! -s /tmp/bad.png
    grep -q 'invalid Mermaid' /tmp/bad.err
    echo "invalid_mermaid=rejected"
    """
)


def run_image_check() -> None:
    from contree_sdk import ContreeSync

    client = ContreeSync()
    image = client.images.use(IMAGE, strict=True)
    result = image.run(shell=CHECK_SCRIPT, disposable=True, timeout=300).wait()
    assert result.exit_code == 0, (
        f"image check failed: exit={result.exit_code}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    out = result.stdout or ""
    for marker in ("file_render=ok", "stdin_render=ok", "invalid_mermaid=rejected"):
        assert marker in out, out
    print(f"image: {IMAGE}")
    print(out.strip())


def run_backend_check() -> None:
    from sandbox_backend import build_sandbox_backend

    backend = build_sandbox_backend(IMAGE)
    written = backend.write("/tmp/oyi-test.txt", "deepagents file tools\n")
    assert not written.error, written
    read = backend.read("/tmp/oyi-test.txt")
    assert not read.error, read
    assert read.file_data["content"] == "deepagents file tools", read.file_data

    result = backend.execute(
        "set -e; printf 'flowchart LR\\n  A[deepagents] --> B[render]\\n' "
        "| render-mermaid -o /tmp/backend.svg --png /tmp/backend.png; "
        "test -s /tmp/backend.svg; grep -q '<svg' /tmp/backend.svg; "
        "python3 -c 'import pathlib; d=pathlib.Path(\"/tmp/backend.png\").read_bytes(); assert d[:8] == b\"\\x89PNG\\r\\n\\x1a\\n\"'; "
        "echo BACKEND_RENDER_OK"
    )
    assert result.exit_code == 0, (
        f"backend execute failed: exit={result.exit_code}\n{result.output}"
    )
    assert "BACKEND_RENDER_OK" in (result.output or ""), result.output
    print("backend: file tools + execute render (SVG + PNG) OK")


async def main() -> None:
    if not LIVE:
        print("sandbox image test skipped; set OYI_LIVE_SANDBOX=1")
        return
    assert os.getenv("NEBIUS_API_KEY"), "NEBIUS_API_KEY is not set"
    assert os.getenv("NEBIUS_PROJECT_ID"), "NEBIUS_PROJECT_ID is not set"

    # The SDK calls are synchronous; keep them off the event loop's critical path.
    await asyncio.to_thread(run_image_check)
    await asyncio.to_thread(run_backend_check)
    print("sandbox image tests passed")


if __name__ == "__main__":
    asyncio.run(main())
