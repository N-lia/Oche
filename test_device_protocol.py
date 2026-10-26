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
    action = parse_action("```json\n{\"type\":\"type\",\"target\":2,\"text\":\"Settings\"}\n```")
    assert action.type == "type"
    assert action.target == 2
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
            "elements": [
                {
                    "index": 1,
                    "text": "Wi-Fi",
                    "kind": "Text",
                    "class": "android.widget.TextView",
                    "id": "android:id/title",
                    "bounds": [0, 100, 720, 200],
                }
            ],
            "element_text": "[1] Text: 'Wi-Fi' | Bounds: [0,42][720,83]",
            "screenshot": "abc",
            "width": 720,
            "height": 1600,
        }
    )
    assert observation.step == 0
    assert observation.elements[0]["text"] == "Wi-Fi"
    assert observation.elements[0]["index"] == 1
    assert "Wi-Fi" in observation.element_text


def test_action_targets_element_index() -> None:
    action = parse_action('{"type":"tap","target":3,"reason":"open it"}')
    assert action.type == "tap"
    assert action.target == 3
    assert action.x is None


def test_tap_needs_a_target_or_coordinates() -> None:
    _expect_parse_error('{"type":"tap"}')
    action = parse_action('{"type":"tap","x":500,"y":250}')
    assert action.x == 500 and action.y == 250


def test_removed_tap_text_action_is_rejected() -> None:
    _expect_parse_error('{"type":"tap_text","text":"Settings"}')


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
    print("device_protocol tests passed")
