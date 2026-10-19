#!/usr/bin/env python3
"""Tests for the ASR server's streaming idle reaper.

Every streaming turn occupies a worker thread in the gRPC pool, so a client
that stops sending audio without closing its stream must not pin that worker
forever. ``_iter_requests`` ends such a stream after the idle timeout.

Run:
    .venv/bin/python test_asr_stream_idle.py
"""

from __future__ import annotations

import sys
import threading
import time

from asr_server import _iter_requests


def test_passthrough_when_timeout_disabled() -> None:
    assert list(_iter_requests(iter([1, 2, 3]), 0)) == [1, 2, 3]


def test_closed_stream_ends_promptly() -> None:
    start = time.monotonic()
    assert list(_iter_requests(iter([]), 5)) == []
    assert time.monotonic() - start < 1.0


def test_silent_stream_is_reaped() -> None:
    release = threading.Event()

    def silent_client():
        yield "config"
        release.wait(5)  # then silence, and never closes the stream

    start = time.monotonic()
    try:
        items = list(_iter_requests(silent_client(), 0.2))
    finally:
        release.set()

    elapsed = time.monotonic() - start
    assert items == ["config"], items
    assert elapsed < 2.0, f"idle reaper took too long: {elapsed:.2f}s"


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
