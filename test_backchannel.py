#!/usr/bin/env python3
"""Self-checking tests for the backchannel controller and clip catalog.

Run:
    .venv/bin/python test_backchannel.py
"""

from __future__ import annotations

import random
import sys

from backchannel import (
    ACK_PHRASES,
    CONTINUE_PHRASES,
    BackchannelController,
    all_phrases,
    clip_key,
)


def _controller() -> BackchannelController:
    return BackchannelController(rng=random.Random(0))


def test_no_backchannel_before_first_after() -> None:
    c = _controller()
    c.start_turn(100.0)
    c.update_activity(100.2, is_speech=False)
    c.update_activity(100.6, is_speech=False)  # pause established
    assert c.tick(100.4) is None, "must wait for first_after before speaking"


def test_backchannel_after_pause() -> None:
    c = _controller()
    c.start_turn(100.0)
    c.update_activity(100.1, is_speech=True)
    c.update_activity(100.5, is_speech=False)
    c.update_activity(100.9, is_speech=False)  # 0.4s silence >= pause
    decision = c.tick(101.2)
    assert decision is not None, "expected an acknowledgement after a pause"
    assert decision.category == "ack"
    assert decision.phrase in ACK_PHRASES


def test_min_gap_between_backchannels() -> None:
    c = _controller()
    c.start_turn(100.0)
    c.update_activity(100.5, is_speech=False)
    c.update_activity(101.0, is_speech=False)
    assert c.tick(101.5) is not None
    # Immediately afterwards no second backchannel is allowed.
    c.update_activity(101.6, is_speech=False)
    c.update_activity(102.0, is_speech=False)
    assert c.tick(102.0) is None, "min_gap must suppress a rapid second backchannel"


def test_long_turn_switches_to_continue() -> None:
    c = _controller()
    c.start_turn(200.0)
    for t in range(0, 21):
        c.update_activity(200.0 + t * 0.5, is_speech=True)
    decision = c.tick(211.0)
    assert decision is not None
    assert decision.category == "continue"
    assert decision.phrase in CONTINUE_PHRASES


def test_max_per_turn_cap() -> None:
    c = _controller()
    c.start_turn(300.0)
    seen = 0
    t = 300.0
    while t < 360.0:
        c.update_activity(t, is_speech=False)
        if c.tick(t) is not None:
            seen += 1
        t += 1.0
    assert seen <= c._max_per_turn, f"emitted {seen} backchannels, cap is {c._max_per_turn}"


def test_suppressed_while_bot_speaking() -> None:
    c = _controller()
    c.start_turn(400.0)
    c.update_activity(400.5, is_speech=False)
    c.update_activity(401.0, is_speech=False)
    assert c.tick(401.5, bot_speaking=True) is None


def test_end_turn_suppresses() -> None:
    c = _controller()
    c.start_turn(500.0)
    c.update_activity(500.5, is_speech=False)
    c.update_activity(501.0, is_speech=False)
    c.end_turn(501.0)
    assert c.tick(501.5) is None


def test_thinking_filler_from_catalog() -> None:
    c = _controller()
    filler = c.thinking_filler(600.0)
    assert filler and filler in all_phrases()


def test_clip_keys_are_stable_and_unique() -> None:
    assert clip_key("mm-hmm") == clip_key("mm-hmm")
    assert clip_key("mm-hmm") != clip_key("right")


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
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
