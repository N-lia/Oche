"""Render Mermaid diagrams inside the OYI sandbox.

The shell already has a correct ``render-mermaid`` command, but asking a model
to compose a shell command is not a guarantee: it can write the Mermaid source
to the requested path with ``write_file`` and then claim the diagram was
rendered. This tool makes the render a typed operation and verifies the bytes
before returning success.
"""

from __future__ import annotations

import posixpath
import re
import shlex
import uuid

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

MAX_SOURCE_CHARS = 50_000
_ALLOWED_ROOTS = ("/tmp/", "/workspace/")


class RenderMermaidInput(BaseModel):
    source: str = Field(
        ...,
        description=(
            "The complete Mermaid diagram source, for example "
            "'flowchart LR\\n  A[Input] --> B[Output]'. Do not wrap it in "
            "Markdown fences."
        ),
    )
    output: str = Field(
        default="/tmp/oyi-diagram.svg",
        description=(
            "Path for the SVG: absolute under /tmp/ or /workspace/, or "
            "relative to /workspace/. Defaults to /tmp/oyi-diagram.svg."
        ),
    )


def _validated_output(output: str) -> str:
    raw = output.strip()
    if not raw.startswith("/"):
        raw = posixpath.join("/workspace", raw)
    path = posixpath.normpath(raw)
    if not path.endswith(".svg"):
        raise ValueError("output must end in .svg")
    if not path.startswith(_ALLOWED_ROOTS):
        raise ValueError("output must be under /tmp/ or /workspace/")
    return path


@tool("render_mermaid", args_schema=RenderMermaidInput)
def render_mermaid(source: str, output: str = "/tmp/oyi-diagram.svg") -> str:
    """Render Mermaid text to SVG in the isolated sandbox and verify the file.

    Use this tool whenever the user asks to render, draw, or produce a Mermaid
    diagram. It writes the source to a temporary file, runs the sandbox's
    Pele-based renderer, reads the result back, and checks that it starts with
    ``<svg`` before reporting success. It never claims success for a missing or
    non-SVG file.
    """
    if not source.strip():
        return "Error: the Mermaid source is empty."
    if len(source) > MAX_SOURCE_CHARS:
        return f"Error: the Mermaid source exceeds {MAX_SOURCE_CHARS} characters."

    try:
        path = _validated_output(output)
    except ValueError as exc:
        return f"Error: {exc}"

    try:
        from sandbox_backend import get_sandbox_backend

        backend = get_sandbox_backend()
    except Exception as exc:  # noqa: BLE001 - reported to the planner
        return f"Error: the Nebius sandbox is not available: {exc}"

    source_path = f"/tmp/oyi-mermaid-{uuid.uuid4().hex[:10]}.mmd"
    try:
        written = backend.write(source_path, source)
    except Exception as exc:  # noqa: BLE001 - reported to the planner
        return f"Error: could not write the Mermaid source: {exc}"
    if written.error:
        return f"Error: could not write the Mermaid source: {written.error}"

    command = (
        f"render-mermaid {shlex.quote(source_path)} -o {shlex.quote(path)}"
        f" && test -s {shlex.quote(path)}"
    )
    try:
        result = backend.execute(command)
    except Exception as exc:  # noqa: BLE001 - reported to the planner
        return f"Error: could not run render-mermaid: {exc}"
    if result.exit_code != 0:
        detail = (result.output or "").strip() or "no output"
        return f"Error: Mermaid rendering failed (exit {result.exit_code}): {detail}"

    # Download the bytes instead of using read(): read() paginates and caps
    # text at ~500 KiB, which could silently truncate a large diagram.
    try:
        downloaded = backend.download_files([path])[0]
    except Exception as exc:  # noqa: BLE001 - reported to the planner
        return f"Error: render-mermaid succeeded but the SVG could not be read: {exc}"
    if downloaded.error:
        return (
            "Error: render-mermaid succeeded but the SVG could not be read: "
            f"{downloaded.error}"
        )
    raw = downloaded.content or b""
    svg = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    if not svg.lstrip().startswith("<svg"):
        return "Error: render-mermaid wrote a file, but its contents are not SVG."

    view_box = re.search(r'viewBox="([^"]+)"', svg)
    dimensions = f", viewBox {view_box.group(1)}" if view_box else ""
    return (
        f"Mermaid rendered to {path} ({len(raw)} bytes{dimensions}). "
        "Verified: the file starts with <svg>. "
        "Reply with this one-line result only: do not read, cat, or paste the SVG "
        "(it contains markup and scripts, and is not chat-safe)."
    )


MERMAID_TOOLS: list[BaseTool] = [render_mermaid]
