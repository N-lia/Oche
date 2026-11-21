"""Wire protocol shared by the OYI Android app and the device gateway.

The protocol is deliberately small and JSON-only:

    phone -> server   hello
    server -> phone   hello_ack
    server -> phone   command
    phone -> server   observation
    server -> phone   action
    server -> phone   command_done
    server -> phone   cancel

The phone is a capability endpoint, not a messaging channel.  Telegram (or a
future WhatsApp/Discord adapter) only submits a ``CommandRequest`` to the
gateway and receives a ``CommandResult`` back.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

ActionType = Literal[
    "tap",
    "long_press",
    "type",
    "press_enter",
    "scroll",
    "back",
    "home",
    "recents",
    "open_app",
    "wait",
    "done",
    "ask_user",
]

_SCREEN_TEXT_LIMIT = 240
_SCREENSHOT_LIMIT = 6 * 1024 * 1024
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LEADING_INDEX_RE = re.compile(r"^\s*\[?\s*(\d{1,3})\s*\]?(?:\s|$|:)")


class DeviceHello(BaseModel):
    """First message sent by the Android app after the WebSocket opens."""

    type: Literal["hello"] = "hello"
    device_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=120)
    android_sdk: int = 0
    app_version: str = Field(default="", max_length=40)
    capabilities: list[str] = Field(default_factory=list)


class Observation(BaseModel):
    """One screen snapshot plus the result of the previous action."""

    type: Literal["observation"] = "observation"
    command_id: str = Field(min_length=1, max_length=128)
    step: int = Field(ge=0, le=64)
    package: str = Field(default="", max_length=255)
    app_label: str = Field(default="", max_length=120)
    # The visible element index (ARTEMIS style): index -> {text, kind, class,
    # id, bounds}.  The phone resolves an index against the live node it
    # captured, so the model never has to guess pixel coordinates.
    elements: list[dict[str, Any]] = Field(default_factory=list)
    # The same list pre-formatted for the model.
    element_text: str = ""
    screenshot: str = ""
    width: int = Field(default=0, ge=0, le=10_000)
    height: int = Field(default=0, ge=0, le=10_000)
    last_action_ok: bool = True
    error: str = Field(default="", max_length=1000)
    action_result: str = Field(default="", max_length=1000)

    @field_validator("screenshot")
    @classmethod
    def _limit_screenshot(cls, value: str) -> str:
        if len(value) > _SCREENSHOT_LIMIT:
            raise ValueError("screenshot is too large")
        return value


class Action(BaseModel):
    """A single UI action chosen by the vision planner.

    Targets follow the mobile-use/ARTEMIS convention: the planner names the
    element it wants (``element_text``) or its index (``target``), and the
    device resolves it against the live screen. Raw ``x``/``y`` (0..1000) are
    only a fallback for custom-drawn UI that exposes no accessible element.
    """

    type: ActionType
    target: int | None = Field(default=None, ge=1, le=1000)
    element_text: str | None = Field(default=None, max_length=500)
    x: int | None = Field(default=None, ge=0, le=1000)
    y: int | None = Field(default=None, ge=0, le=1000)
    text: str | None = Field(default=None, max_length=4000)
    submit: bool | None = None
    package: str | None = Field(default=None, max_length=255)
    direction: Literal["up", "down", "left", "right"] | None = None
    distance: float | None = Field(default=None, ge=0.1, le=1.0)
    seconds: float | None = Field(default=None, ge=0.2, le=5.0)
    message: str = Field(default="", max_length=1000)
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _require_action_arguments(self) -> "Action":
        if self.type in {"tap", "long_press"} and not (
            self.target is not None
            or (self.element_text or "").strip()
            or (self.x is not None and self.y is not None)
        ):
            raise ValueError(f"{self.type} needs an element index, an element description or x/y")
        if self.type == "type" and not (self.text or "").strip():
            raise ValueError("type requires text")
        if self.type == "open_app" and not (self.package or "").strip():
            raise ValueError("open_app requires a package")
        return self


# The planner is a phone-use model: it naturally answers in the mobile-use /
# ARTEMIS vocabulary. Accept it as-is and normalise it.
_ACTION_TYPE_ALIASES: dict[str, str] = {
    "click": "tap",
    "tap": "tap",
    "press": "tap",
    "long_press": "long_press",
    "long_click": "long_press",
    "hold": "long_press",
    "input": "type",
    "type": "type",
    "input_text": "type",
    "enter_text": "type",
    "set_text": "type",
    "press_enter": "press_enter",
    "enter": "press_enter",
    "submit": "press_enter",
    "ime_action": "press_enter",
    "search": "press_enter",
    "scroll": "scroll",
    "swipe": "scroll",
    "drag": "scroll",
    "open_app": "open_app",
    "launch": "open_app",
    "launch_app": "open_app",
    "start_app": "open_app",
    "navigate": "open_app",
    "back": "back",
    "go_back": "back",
    "navigate_back": "back",
    "home": "home",
    "go_home": "home",
    "navigate_home": "home",
    "recents": "recents",
    "recent_apps": "recents",
    "app_switch": "recents",
    "wait": "wait",
    "sleep": "wait",
    "done": "done",
    "finish": "done",
    "terminate": "done",
    "complete": "done",
    "answer": "done",
    "ask_user": "ask_user",
    "ask": "ask_user",
    "clarify": "ask_user",
    "take_over": "ask_user",
}


class CommandRequest(BaseModel):
    """Request submitted by a channel adapter or the agent tool."""

    text: str = Field(min_length=1, max_length=4000)
    device_id: str = Field(default="", max_length=128)
    max_steps: int = Field(default=16, ge=1, le=32)
    timeout_s: float = Field(default=180.0, ge=10.0, le=300.0)
    thread_id: str = Field(default="device", max_length=128)


class CommandResult(BaseModel):
    command_id: str
    device_id: str
    status: Literal["completed", "timeout", "error", "cancelled", "busy"]
    message: str = ""
    steps: int = 0
    error: str = ""


class DeviceSummary(BaseModel):
    device_id: str
    name: str = ""
    model: str = ""
    android_sdk: int = 0
    app_version: str = ""
    capabilities: list[str] = Field(default_factory=list)
    connected: bool = False
    last_seen: float = 0.0
    active_command_id: str = ""


class ActionParseError(ValueError):
    """The model returned something that is not a valid Action."""


def _extract_json_payload(raw_text: str) -> Any:
    text = _THINK_TAG_RE.sub("", raw_text or "").strip()
    if not text:
        raise ActionParseError("model returned an empty action")

    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        candidate_text = fenced.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        candidate_text = text[start : end + 1] if (start >= 0 and end > start) else text

    try:
        return json.loads(candidate_text)
    except json.JSONDecodeError:
        pass

    # If prose before the JSON contained braces, scan each '{' with raw_decode.
    decoder = json.JSONDecoder()
    decoded_dicts: list[dict[str, Any]] = []
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            if isinstance(obj, dict):
                decoded_dicts.append(obj)
        except json.JSONDecodeError:
            continue
    if decoded_dicts:
        for obj in reversed(decoded_dicts):
            if any(k in obj for k in ("type", "action_type", "actionType", "action")):
                return obj
        return decoded_dicts[-1]

    # Phone-use models sometimes stop one character early or leave a stray
    # quote. Repair the obvious cases instead of failing the whole command.
    candidates = (
        candidate_text,
        candidate_text + "}",
        candidate_text.rstrip('"') + "}",
        candidate_text.rstrip('",') + "}",
        candidate_text + '"}',
        candidate_text.rstrip('"') + '"}',
        candidate_text.rstrip('"') + "]}",
    )
    last_exc: Exception | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_exc = exc
            continue
    raise ActionParseError(f"model did not return JSON: {last_exc}") from last_exc


def parse_action(raw: str | dict[str, Any] | Action) -> Action:
    """Parse a planner response into a validated action.

    Phone-use models answer in the mobile-use / ARTEMIS vocabulary
    (``action_type`` + ``action_ui_element`` + ``x``/``y``), while an index
    based answer (``{"type": "tap", "target": 3}``) is also accepted. Both are
    normalised into one :class:`Action`.
    """

    if isinstance(raw, Action):
        return raw
    if isinstance(raw, dict):
        payload: Any = raw
    else:
        payload = _extract_json_payload(raw)

    if not isinstance(payload, dict):
        raise ActionParseError("action must be a JSON object")

    try:
        return Action.model_validate(_normalize_action(payload))
    except ValidationError as exc:
        raise ActionParseError(f"invalid action: {exc}") from exc


def _coerce_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 1000 else None
    if isinstance(value, str):
        match = _LEADING_INDEX_RE.match(value.strip())
        if match:
            idx = int(match.group(1))
            return idx if 1 <= idx <= 1000 else None
    return None


def _normalize_action(payload: dict[str, Any]) -> dict[str, Any]:
    """Map the mobile-use/ARTEMIS action shape onto our own."""
    out = dict(payload)

    raw_type = out.get("type") or out.get("action_type") or out.get("actionType")
    if raw_type is None:
        # The legacy envelope {"action": "open_app", ...}. In the mobile-use
        # vocabulary "action" is a human description instead, so only treat it
        # as a type when it names one.
        legacy = out.get("action")
        if isinstance(legacy, str) and legacy.strip().lower() in _ACTION_TYPE_ALIASES:
            raw_type = legacy
    if isinstance(raw_type, str):
        mapped = _ACTION_TYPE_ALIASES.get(raw_type.strip().lower())
        if mapped is None:
            raise ActionParseError(f"unknown action type {raw_type!r}")
        out["type"] = mapped

    # Normalize element index from target / index / element_index / element_id.
    for key in ("target", "index", "element_index", "element_id"):
        if key in out and out.get(key) is not None:
            coerced = _coerce_index(out.get(key))
            if coerced is not None:
                out["target"] = coerced
                break

    # The element the model wants, described in words.
    for key in (
        "action_ui_element",
        "ui_element",
        "element",
        "element_description",
        "target_description",
        "label",
    ):
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            out["element_text"] = value.strip()
            break
    else:
        # The mobile-use vocabulary sometimes puts the element description in
        # "action" itself ("the text 'Settings' in the list").
        legacy_description = out.get("action")
        if (
            isinstance(legacy_description, str)
            and legacy_description.strip()
            and legacy_description.strip().lower() not in _ACTION_TYPE_ALIASES
        ):
            out["element_text"] = legacy_description.strip()
            if not out.get("reason"):
                out["reason"] = legacy_description.strip()

    if isinstance(out.get("action"), str) and out["action"].strip() and not out.get("reason"):
        if out["action"].strip().lower() not in _ACTION_TYPE_ALIASES:
            out["reason"] = out["action"].strip()

    # If element_text begins with a bracketed index like "[3] Text: 'Wi-Fi'",
    # also populate target=3 when target was not given explicitly.
    if out.get("target") is None and isinstance(out.get("element_text"), str):
        et = out["element_text"].strip()
        if et.startswith("[") and "]" in et:
            coerced = _coerce_index(et)
            if coerced is not None:
                out["target"] = coerced

    # Support [x, y] coordinate arrays (point / coordinate / coordinates).
    if out.get("x") is None or out.get("y") is None:
        for coord_key in ("point", "coordinate", "coordinates", "position"):
            coord = out.get(coord_key)
            if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                out["x"], out["y"] = coord[0], coord[1]
                break

    # mobile-use emits 0..1 fractions, our protocol is 0..1000.
    x, y = out.get("x"), out.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        if 0 <= float(x) <= 1 and 0 <= float(y) <= 1 and (isinstance(x, float) or isinstance(y, float) or float(x) <= 1.0 and float(y) <= 1.0):
            # Treat (0..1, 0..1) as normalized fractions unless both are > 1.
            if float(x) <= 1.0 and float(y) <= 1.0 and not (isinstance(x, int) and isinstance(y, int) and (x > 1 or y > 1)):
                out["x"] = int(round(float(x) * 1000))
                out["y"] = int(round(float(y) * 1000))
            else:
                out["x"] = int(round(float(x)))
                out["y"] = int(round(float(y)))
        else:
            out["x"] = int(round(float(x)))
            out["y"] = int(round(float(y)))

    # App to open.
    for key in ("package", "app_name", "app", "package_name"):
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            out["package"] = value.strip()
            break

    # The final answer.
    if out.get("type") in {"done", "ask_user"}:
        for key in ("message", "final_answer", "answer", "text", "reason"):
            value = out.get(key)
            if isinstance(value, str) and value.strip():
                out["message"] = value.strip()
                break

    # "input" carries its text; a click carries the element description.
    if out.get("type") == "type":
        if not out.get("text"):
            for key in ("input_text", "text_to_type", "value", "content"):
                value = out.get(key)
                if isinstance(value, str) and value.strip():
                    out["text"] = value.strip()
                    break
        for submit_key in ("submit", "press_enter", "enter"):
            if isinstance(out.get(submit_key), bool):
                out["submit"] = out[submit_key]
                break

    for drop_key in (
        "action_type",
        "actionType",
        "action_ui_element",
        "ui_element",
        "element",
        "element_description",
        "target_description",
        "action",
        "index",
        "element_index",
        "element_id",
        "point",
        "coordinate",
        "coordinates",
        "position",
        "app_name",
        "app",
        "package_name",
        "input_text",
        "text_to_type",
    ):
        out.pop(drop_key, None)
    return out


def action_for_device(action: Action) -> dict[str, Any]:
    """Serialize an action for the Android executor, omitting null fields."""

    return action.model_dump(exclude_none=True)


def command_result_from_session(
    command_id: str,
    device_id: str,
    status: str,
    message: str,
    steps: int,
    error: str = "",
) -> CommandResult:
    """Build a result with a validated status literal."""

    allowed = {"completed", "timeout", "error", "cancelled", "busy"}
    return CommandResult(
        command_id=command_id,
        device_id=device_id,
        status=status if status in allowed else "error",  # type: ignore[arg-type]
        message=message,
        steps=steps,
        error=error,
    )
