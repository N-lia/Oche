#!/usr/bin/env python3
"""Tests for the diagram tool (diagram.py).

Run:
    .venv/bin/python test_diagram.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("NEBIUS_API_KEY", "test-key")

from diagram import DIAGRAM_TOOLS, _extract_svg, _render_via_sandbox, get_sandbox_url  # noqa: E402


def test_diagram_tool_registered() -> None:
    assert any(t.name == "render_diagram" for t in DIAGRAM_TOOLS)


def test_extract_svg_shapes() -> None:
    assert _extract_svg({"svg": "<svg>...</svg>"}) == "<svg>...</svg>"
    assert _extract_svg({"image_url": "https://example.com/d.svg"}) == "https://example.com/d.svg"
    assert _extract_svg({"url": "https://example.com/d.svg"}) == "https://example.com/d.svg"
    assert _extract_svg({"nope": "<svg>"}) is None
    assert _extract_svg("not a dict") is None


def test_no_sandbox_url_returns_clear_error() -> None:
    # No NEBIUS_SANDBOX_URL set -> clear, actionable error, no crash.
    import asyncio

    import diagram as d

    d._HTTP_CLIENT = None
    result = asyncio.run(_render_via_sandbox("flowchart TD\n A --> B"))
    assert result["ok"] is False
    assert result["svg"] is None
    assert "NEBIUS_SANDBOX_URL" in result["error"]


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"\033[92m✓ {test.__name__}\033[0m")
        except AssertionError as exc:
            failures += 1
            print(f"\033[91m✗ {test.__name__}: {exc}\033[0m")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"\033[91m✗ {test.__name__}: {type(exc).__name__}: {exc}\033[0m")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
