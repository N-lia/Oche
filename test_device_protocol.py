#!/usr/bin/env python3
"""Tests for the phone-control wire protocol and action parser.

Run:
    .venv/bin/python test_device_protocol.py
"""

from __future__ import annotations

from device_protocol import ActionParseError, Observation, parse_action


def _expect_parse_error(raw: str) -> None:
    try:
        parse_action(raw)
    except ActionParseError:
        return
    raise AssertionError(f"expected ActionParseError for {raw!r}")


def test_parse_plain_action() -> None:
    action = parse_action('{"type":"tap","x":500,"y":250,"reason":"button"}')
    assert action.type == "tap"
    assert action.x == 500
    assert action.y == 250


def test_parse_fenced_action() -> None:
    action = parse_action("```json\n{\"type\":\"tap_text\",\"text\":\"Settings\"}\n```")
    assert action.type == "tap_text"
    assert action.text == "Settings"


def test_parse_action_envelope() -> None:
    action = parse_action('{"action":"open_app","package":"com.android.settings"}')
    assert action.type == "open_app"
    assert action.package == "com.android.settings"


def test_tap_requires_coordinates() -> None:
    _expect_parse_error('{"type":"tap"}')


def test_type_requires_text() -> None:
    _expect_parse_error('{"type":"type","text":"   "}')


def test_observation_roundtrip() -> None:
    observation = Observation.model_validate(
        {
            "type": "observation",
            "command_id": "cmd_1",
            "step": 0,
            "package": "com.android.settings",
            "app_label": "Settings",
            "tree": [{"text": "Wi-Fi", "bounds": [0, 100, 720, 200]}],
            "screenshot": "abc",
            "width": 720,
            "height": 1600,
        }
    )
    assert observation.step == 0
    assert observation.tree[0]["text"] == "Wi-Fi"


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
    print("device_protocol tests passed")
