#!/usr/bin/env python3
"""Unit tests for the render_mermaid tool (no Nebius access needed).

The fake backend records what the tool does and returns a canned SVG, so these
tests verify the important guarantee: the tool only reports success after it
has downloaded bytes that actually start with <svg>.
"""

from __future__ import annotations

from types import SimpleNamespace

import mermaid_tool
import sandbox_backend


class FakeBackend:
    def __init__(self, *, exit_code: int = 0, output: str = "", svg: bytes | None = None):
        self.exit_code = exit_code
        self.output = output
        self.svg = svg if svg is not None else b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 20"></svg>'
        self.written: dict[str, str] = {}
        self.command: str | None = None
        self.downloaded: list[str] | None = None

    def write(self, path: str, content: str) -> SimpleNamespace:
        self.written[path] = content
        return SimpleNamespace(error=None)

    def execute(self, command: str) -> SimpleNamespace:
        self.command = command
        return SimpleNamespace(exit_code=self.exit_code, output=self.output)

    def download_files(self, paths: list[str]) -> list[SimpleNamespace]:
        self.downloaded = paths
        return [SimpleNamespace(path=paths[0], content=self.svg, error=None)]


def _with_backend(backend: FakeBackend):
    original = sandbox_backend.get_sandbox_backend
    sandbox_backend.get_sandbox_backend = lambda: backend
    return original


def test_valid_render() -> None:
    backend = FakeBackend()
    original = _with_backend(backend)
    try:
        reply = mermaid_tool.render_mermaid.invoke(
            {"source": "flowchart LR\n  A --> B", "output": "/tmp/test.svg"}
        )
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "Verified" in reply, reply
    assert "/tmp/test.svg" in reply, reply
    assert "viewBox 0 0 10 20" in reply, reply
    assert backend.command and "render-mermaid" in backend.command, backend.command
    assert backend.downloaded == ["/tmp/test.svg"], backend.downloaded
    assert list(backend.written.values()) == ["flowchart LR\n  A --> B"], backend.written


def test_invalid_mermaid_is_an_error() -> None:
    backend = FakeBackend(
        exit_code=2,
        output="render-mermaid: invalid Mermaid: Parse error on line 2:\n  A -->\n",
    )
    original = _with_backend(backend)
    try:
        reply = mermaid_tool.render_mermaid.invoke(
            {"source": "flowchart TD\n  A -->", "output": "/tmp/bad.svg"}
        )
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert reply.startswith("Error:"), reply
    assert "invalid Mermaid" in reply, reply
    assert "Parse error" in reply, reply


def test_source_written_as_svg_is_rejected() -> None:
    backend = FakeBackend(svg=b"flowchart LR\n  A --> B\n")
    original = _with_backend(backend)
    try:
        reply = mermaid_tool.render_mermaid.invoke(
            {"source": "flowchart LR\n  A --> B", "output": "/tmp/not-svg.svg"}
        )
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "not SVG" in reply, reply


def test_relative_output_is_workspace_relative() -> None:
    backend = FakeBackend()
    original = _with_backend(backend)
    try:
        reply = mermaid_tool.render_mermaid.invoke(
            {"source": "flowchart LR\n  A --> B", "output": "diagram.svg"}
        )
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "/workspace/diagram.svg" in reply, reply
    assert backend.downloaded == ["/workspace/diagram.svg"], backend.downloaded


def test_output_path_is_confined() -> None:
    backend = FakeBackend()
    original = _with_backend(backend)
    try:
        reply = mermaid_tool.render_mermaid.invoke(
            {"source": "flowchart LR\n  A --> B", "output": "/etc/passwd.svg"}
        )
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "under /tmp/ or /workspace/" in reply, reply
    assert not backend.written, backend.written


def test_empty_source_is_rejected() -> None:
    reply = mermaid_tool.render_mermaid.invoke({"source": "   "})
    assert "empty" in reply, reply


def test_missing_backend_is_reported() -> None:
    original = sandbox_backend.get_sandbox_backend

    def unavailable():
        raise RuntimeError("image oyi-sandbox:latest is unavailable")

    sandbox_backend.get_sandbox_backend = unavailable
    try:
        reply = mermaid_tool.render_mermaid.invoke(
            {"source": "flowchart LR\n  A --> B", "output": "/tmp/x.svg"}
        )
    finally:
        sandbox_backend.get_sandbox_backend = original

    assert "not available" in reply, reply
    assert "unavailable" in reply, reply


if __name__ == "__main__":
    test_valid_render()
    test_invalid_mermaid_is_an_error()
    test_source_written_as_svg_is_rejected()
    test_relative_output_is_workspace_relative()
    test_output_path_is_confined()
    test_empty_source_is_rejected()
    test_missing_backend_is_reported()
    print("mermaid tool tests passed")
