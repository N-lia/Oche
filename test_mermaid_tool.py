#!/usr/bin/env python3
"""Unit tests for the render_mermaid tool (no Nebius access needed).

The fake backend records what the tool does and returns canned SVG/PNG bytes,
so these tests verify the important guarantees: the tool only reports success
after it has downloaded bytes that actually start with <svg>/PNG, and the PNG
is delivered to Telegram when a sender is registered.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import mermaid_tool
import sandbox_backend
from memory import reset_current_user_id, set_current_user_id

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 20"></svg>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeBackend:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        output: str = "",
        svg: bytes | None = None,
        png: bytes | None = None,
    ):
        self.exit_code = exit_code
        self.output = output
        self.svg = svg if svg is not None else SVG
        self.png = png if png is not None else PNG
        self.written: dict[str, str] = {}
        self.command: str | None = None
        self.downloaded: list[str] = []

    def write(self, path: str, content: str) -> SimpleNamespace:
        self.written[path] = content
        return SimpleNamespace(error=None)

    def execute(self, command: str) -> SimpleNamespace:
        self.command = command
        return SimpleNamespace(exit_code=self.exit_code, output=self.output)

    def download_files(self, paths: list[str]) -> list[SimpleNamespace]:
        results: list[SimpleNamespace] = []
        for path in paths:
            self.downloaded.append(path)
            content = self.png if path.endswith(".png") else self.svg
            results.append(SimpleNamespace(path=path, content=content, error=None))
        return results


def _with_backend(backend: FakeBackend):
    original = sandbox_backend.get_sandbox_backend
    sandbox_backend.get_sandbox_backend = lambda: backend
    return original


async def _invoke(args: dict, chat_id: str | None = None):
    token = set_current_user_id(chat_id) if chat_id is not None else None
    try:
        return await mermaid_tool.render_mermaid.ainvoke(args)
    finally:
        if token is not None:
            reset_current_user_id(token)


def run(args: dict, chat_id: str | None = None):
    return asyncio.run(_invoke(args, chat_id))


def test_valid_render_without_telegram() -> None:
    backend = FakeBackend()
    original = _with_backend(backend)
    try:
        reply = run({"source": "flowchart LR\n  A --> B", "output": "/tmp/test.svg"})
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "Mermaid rendered" in reply, reply
    assert "/tmp/test.svg" in reply, reply
    assert "/tmp/test.png" in reply, reply
    assert "viewBox 0 0 10 20" in reply, reply
    assert "No chat image delivery" in reply, reply
    assert backend.command and "render-mermaid" in backend.command, backend.command
    assert "--png" in backend.command, backend.command
    assert backend.downloaded == ["/tmp/test.svg", "/tmp/test.png"], backend.downloaded
    assert list(backend.written.values()) == ["flowchart LR\n  A --> B"], backend.written


def test_valid_render_delivers_telegram_photo() -> None:
    backend = FakeBackend()
    calls: list[tuple[object, bytes, str]] = []

    async def sender(chat_id, png_bytes, caption):
        calls.append((chat_id, png_bytes, caption))

    original = _with_backend(backend)
    previous_sender = mermaid_tool._TELEGRAM_PHOTO_SENDER
    mermaid_tool.register_telegram_photo_sender(sender)
    try:
        reply = run(
            {
                "source": "flowchart LR\n  A --> B",
                "output": "/tmp/sent.svg",
                "caption": "  A   diagram  ",
            },
            chat_id="12345",
        )
    finally:
        sandbox_backend.get_sandbox_backend = original
        mermaid_tool._TELEGRAM_PHOTO_SENDER = previous_sender

    assert "delivered to Telegram" in reply, reply
    assert len(calls) == 1, calls
    chat_id, png_bytes, caption = calls[0]
    assert chat_id == "12345", chat_id
    assert png_bytes == PNG, png_bytes[:8]
    assert caption == "A diagram", caption


def test_telegram_delivery_failure_is_reported() -> None:
    backend = FakeBackend()

    async def sender(chat_id, png_bytes, caption):
        raise RuntimeError("telegram down")

    original = _with_backend(backend)
    previous_sender = mermaid_tool._TELEGRAM_PHOTO_SENDER
    mermaid_tool.register_telegram_photo_sender(sender)
    try:
        reply = run(
            {"source": "flowchart LR\n  A --> B", "output": "/tmp/fail.svg"},
            chat_id="12345",
        )
    finally:
        sandbox_backend.get_sandbox_backend = original
        mermaid_tool._TELEGRAM_PHOTO_SENDER = previous_sender

    assert "Telegram photo delivery failed" in reply, reply
    assert "telegram down" in reply, reply


def test_empty_output_gets_a_unique_path() -> None:
    backend = FakeBackend()
    original = _with_backend(backend)
    try:
        first = run({"source": "flowchart LR\n  A --> B"})
        second = run({"source": "flowchart LR\n  A --> B"})
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "/tmp/oyi-diagram-" in first, first
    assert "/tmp/oyi-diagram-" in second, second
    first_path = re.search(r"/tmp/oyi-diagram-[0-9a-f]+\.svg", first)
    second_path = re.search(r"/tmp/oyi-diagram-[0-9a-f]+\.svg", second)
    assert first_path and second_path, (first, second)
    assert first_path.group(0) != second_path.group(0), (first, second)


def test_invalid_mermaid_is_an_error() -> None:
    backend = FakeBackend(
        exit_code=2,
        output="render-mermaid: invalid Mermaid: Parse error on line 2:\n  A -->\n",
    )
    original = _with_backend(backend)
    try:
        reply = run({"source": "flowchart TD\n  A -->", "output": "/tmp/bad.svg"})
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert reply.startswith("Error:"), reply
    assert "invalid Mermaid" in reply, reply
    assert "Parse error" in reply, reply


def test_source_written_as_svg_is_rejected() -> None:
    backend = FakeBackend(svg=b"flowchart LR\n  A --> B\n")
    original = _with_backend(backend)
    try:
        reply = run({"source": "flowchart LR\n  A --> B", "output": "/tmp/not-svg.svg"})
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "not SVG" in reply, reply


def test_missing_png_is_rejected() -> None:
    backend = FakeBackend(png=b"not a png")
    original = _with_backend(backend)
    try:
        reply = run({"source": "flowchart LR\n  A --> B", "output": "/tmp/not-png.svg"})
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "not a PNG" in reply, reply


def test_relative_output_is_workspace_relative() -> None:
    backend = FakeBackend()
    original = _with_backend(backend)
    try:
        reply = run({"source": "flowchart LR\n  A --> B", "output": "diagram.svg"})
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "/workspace/diagram.svg" in reply, reply
    assert backend.downloaded == ["/workspace/diagram.svg", "/workspace/diagram.png"], backend.downloaded


def test_output_path_is_confined() -> None:
    backend = FakeBackend()
    original = _with_backend(backend)
    try:
        reply = run({"source": "flowchart LR\n  A --> B", "output": "/etc/passwd.svg"})
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "under /tmp/ or /workspace/" in reply, reply
    assert not backend.written, backend.written


def test_empty_source_is_rejected() -> None:
    reply = run({"source": "   "})
    assert "empty" in reply, reply


def test_missing_backend_is_reported() -> None:
    original = sandbox_backend.get_sandbox_backend

    def unavailable():
        raise RuntimeError("image oyi-sandbox:latest is unavailable")

    sandbox_backend.get_sandbox_backend = unavailable
    try:
        reply = run({"source": "flowchart LR\n  A --> B", "output": "/tmp/x.svg"})
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "not available" in reply, reply
    assert "unavailable" in reply, reply


if __name__ == "__main__":
    test_valid_render_without_telegram()
    test_valid_render_delivers_telegram_photo()
    test_telegram_delivery_failure_is_reported()
    test_invalid_mermaid_is_an_error()
    test_source_written_as_svg_is_rejected()
    test_missing_png_is_rejected()
    test_relative_output_is_workspace_relative()
    test_empty_output_gets_a_unique_path()
    test_output_path_is_confined()
    test_empty_source_is_rejected()
    test_missing_backend_is_reported()
    print("mermaid tool tests passed")
