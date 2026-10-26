"""Render Mermaid diagrams inside the OYI sandbox and deliver them.

The shell already has a correct ``render-mermaid`` command, but asking a model
to compose a shell command is not a guarantee: it can write the Mermaid source
to the requested path with ``write_file`` and then claim the diagram was
rendered. This tool makes the render a typed operation and verifies the bytes
before returning success.

It also rasterizes the SVG to PNG and, when a Telegram photo sender is
registered (the bot process), delivers the PNG to the current chat. A sandbox
path alone is not a diagram the user can see.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import re
import shlex
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from memory import _CURRENT_USER_ID

MAX_SOURCE_CHARS = 50_000
MAX_CAPTION_CHARS = 1000
_ALLOWED_ROOTS = ("/tmp/", "/workspace/")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

TelegramPhotoSender = Callable[[int | str, bytes, str], Awaitable[None]]
_TELEGRAM_PHOTO_SENDER: TelegramPhotoSender | None = None


def register_telegram_photo_sender(sender: TelegramPhotoSender) -> None:
    """Register Telegram photo delivery (only the bot process has one)."""
    global _TELEGRAM_PHOTO_SENDER
    _TELEGRAM_PHOTO_SENDER = sender


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
        default="",
        description=(
            "Path for the SVG: absolute under /tmp/ or /workspace/, or "
            "relative to /workspace/. Leave empty for a unique "
            "/tmp/oyi-diagram-<id>.svg. The PNG is written next to it with the "
            "same name and a .png suffix."
        ),
    )
    caption: str = Field(
        default="Mermaid diagram",
        description=(
            "Short caption for the delivered image (max 1000 characters). "
            "Plain text only."
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


def _png_path(svg_path: str) -> str:
    return f"{svg_path[:-4]}.png"


def _download(backend: Any, path: str) -> bytes:
    downloaded = backend.download_files([path])[0]
    if downloaded.error:
        raise RuntimeError(f"could not read {path}: {downloaded.error}")
    content = downloaded.content
    if content is None:
        raise RuntimeError(f"{path} is empty")
    return content if isinstance(content, bytes) else str(content).encode("utf-8")


def _clean_caption(caption: str) -> str:
    text = " ".join(caption.split())
    return text[:MAX_CAPTION_CHARS] or "Mermaid diagram"


@tool("render_mermaid", args_schema=RenderMermaidInput)
async def render_mermaid(
    source: str,
    output: str = "",
    caption: str = "Mermaid diagram",
) -> str:
    """Render Mermaid text to a verified SVG and PNG and deliver the image.

    Use this tool whenever the user asks to render, draw, or produce a Mermaid
    diagram. It writes the source to a temporary file, runs the sandbox's
    Pele-based renderer, downloads both files, checks that the SVG starts with
    ``<svg`` and the PNG starts with the PNG signature, and only then reports
    success. On Telegram it also sends the PNG to the current chat as a photo.
    It never claims success for a missing or malformed file.
    """
    if not source.strip():
        return "Error: the Mermaid source is empty."
    if len(source) > MAX_SOURCE_CHARS:
        return f"Error: the Mermaid source exceeds {MAX_SOURCE_CHARS} characters."

    if not output.strip():
        output = f"/tmp/oyi-diagram-{uuid.uuid4().hex[:8]}.svg"
    try:
        path = _validated_output(output)
    except ValueError as exc:
        return f"Error: {exc}"
    png_path = _png_path(path)

    try:
        from sandbox_backend import get_sandbox_backend

        backend = get_sandbox_backend()
    except Exception as exc:  # noqa: BLE001 - reported to the planner
        return f"Error: the Nebius sandbox is not available: {exc}"

    source_path = f"/tmp/oyi-mermaid-{uuid.uuid4().hex[:10]}.mmd"
    try:
        written = await asyncio.to_thread(backend.write, source_path, source)
    except Exception as exc:  # noqa: BLE001 - reported to the planner
        return f"Error: could not write the Mermaid source: {exc}"
    if written.error:
        return f"Error: could not write the Mermaid source: {written.error}"

    command = (
        f"render-mermaid {shlex.quote(source_path)} -o {shlex.quote(path)}"
        f" --png {shlex.quote(png_path)}"
    )
    try:
        result = await asyncio.to_thread(backend.execute, command)
    except Exception as exc:  # noqa: BLE001 - reported to the planner
        return f"Error: could not run render-mermaid: {exc}"
    if result.exit_code != 0:
        detail = (result.output or "").strip() or "no output"
        return f"Error: Mermaid rendering failed (exit {result.exit_code}): {detail}"

    try:
        svg_raw = await asyncio.to_thread(_download, backend, path)
        png_raw = await asyncio.to_thread(_download, backend, png_path)
    except Exception as exc:  # noqa: BLE001 - reported to the planner
        return f"Error: render-mermaid succeeded but its output could not be read: {exc}"

    if not svg_raw.lstrip().startswith(b"<svg"):
        return "Error: render-mermaid wrote a file, but its contents are not SVG."
    if not png_raw.startswith(_PNG_SIGNATURE):
        return "Error: render-mermaid wrote a PNG path, but its contents are not a PNG."

    view_box = re.search(rb'viewBox="([^"]+)"', svg_raw)
    dimensions = f", viewBox {view_box.group(1).decode()}" if view_box else ""
    summary = (
        f"Mermaid rendered to {path} and {png_path} "
        f"({len(svg_raw)} bytes SVG, {len(png_raw)} bytes PNG{dimensions})."
    )

    if _TELEGRAM_PHOTO_SENDER is None:
        return (
            f"{summary} No chat image delivery is registered on this surface; "
            "report the paths only. Do not paste the SVG."
        )

    chat_id = _CURRENT_USER_ID.get() or os.environ.get("ALLOWED_USER_ID", "").strip()
    if not chat_id:
        return f"{summary} The Telegram chat id is unknown, so the image was not delivered."
    if str(chat_id).strip().lower() in {"me", "unknown", "none"}:
        return f"{summary} No Telegram chat is associated with this turn."

    try:
        await _TELEGRAM_PHOTO_SENDER(chat_id, png_raw, _clean_caption(caption))
    except Exception as exc:  # noqa: BLE001 - reported to the planner
        return f"{summary} Telegram photo delivery failed: {exc}"

    return (
        f"{summary} The PNG was delivered to Telegram as a photo. "
        "Reply with a one-line confirmation; do not paste the SVG."
    )


MERMAID_TOOLS: list[BaseTool] = [render_mermaid]
