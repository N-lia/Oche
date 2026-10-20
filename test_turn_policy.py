#!/usr/bin/env python3
"""Tests for live-voice turn classification (continuers vs. real turns).

Run:
    .venv/bin/python test_turn_policy.py
"""

from __future__ import annotations

import sys

from turn_policy import classify_turn, normalize


def test_normalize() -> None:
    assert normalize("  Mm-Hmm!! ") == "mm-hmm"
    assert normalize("Okay…") == "okay"


def test_empty() -> None:
    assert classify_turn("") == "empty"
    assert classify_turn("   ") == "empty"
    assert classify_turn("...") == "empty"


def test_backchannels_never_answer() -> None:
    for text in ("hmm", "mm-hmm", "uh-huh", "right", "oh", "go on"):
        assert classify_turn(text) == "continuation", text
        assert classify_turn(text, expecting_answer=True) == "continuation", text


def test_acknowledgements_are_continuers() -> None:
    for text in ("ok", "okay", "yeah", "yep", "sure", "got it", "i see", "alright"):
        assert classify_turn(text) == "continuation", text


def test_acknowledgements_answer_a_question() -> None:
    for text in ("ok", "okay", "yeah", "sure", "got it"):
        assert classify_turn(text, expecting_answer=True) == "turn", text


def test_yes_no_are_answers() -> None:
    assert classify_turn("yes") == "turn"
    assert classify_turn("no") == "turn"
    assert classify_turn("nope") == "turn"


def test_questions_and_requests_are_turns() -> None:
    assert classify_turn("what time is it?") == "turn"
    assert classify_turn("book me a table") == "turn"
    assert classify_turn("remind me tomorrow") == "turn"
    assert classify_turn("ok but book it for eight") == "turn"


def test_repeated_fillers_are_continuers() -> None:
    assert classify_turn("ok ok") == "continuation"
    assert classify_turn("mm hmm") == "continuation"
    assert classify_turn("yeah right") == "continuation"


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
