#!/usr/bin/env python3
"""TTS provider selection tests (Chatterbox vs CosyVoice 2).

Both Modal apps speak the same HTTP contract, so switching is purely an
environment decision. These tests pin that decision and the failure message
when the selected provider has no URL.

Run:
    .venv/bin/python test_tts_provider.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from voice import get_tts_provider, get_tts_url, synthesize_speech

_KEYS = ("TTS_PROVIDER", "CHATTERBOX_MODAL_URL", "COSYVOICE_MODAL_URL")


def _set_env(**values: str | None):
    saved = {key: os.environ.get(key) for key in _KEYS}
    for key in _KEYS:
        os.environ.pop(key, None)
    for key, value in values.items():
        if value is not None:
            os.environ[key] = value
    return saved


def _restore(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_chatterbox_is_the_default() -> None:
    saved = _set_env(CHATTERBOX_MODAL_URL="https://chatterbox.modal.run")
    try:
        assert get_tts_provider() == "chatterbox"
        assert get_tts_url() == "https://chatterbox.modal.run"
    finally:
        _restore(saved)


def test_cosyvoice_provider_switch() -> None:
    saved = _set_env(
        TTS_PROVIDER="cosyvoice",
        CHATTERBOX_MODAL_URL="https://chatterbox.modal.run",
        COSYVOICE_MODAL_URL=" https://cosy.modal.run ",
    )
    try:
        assert get_tts_provider() == "cosyvoice"
        assert get_tts_url() == "https://cosy.modal.run", "URLs are trimmed"
    finally:
        _restore(saved)


def test_cosyvoice_without_url_fails_clearly() -> None:
    saved = _set_env(
        TTS_PROVIDER="cosyvoice",
        CHATTERBOX_MODAL_URL="https://chatterbox.modal.run",
    )
    try:
        assert get_tts_url() == ""
        try:
            asyncio.run(synthesize_speech("hello"))
        except ValueError as exc:
            assert "COSYVOICE_MODAL_URL" in str(exc), exc
        else:
            raise AssertionError("a missing CosyVoice URL must raise")
    finally:
        _restore(saved)


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
