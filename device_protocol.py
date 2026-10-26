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
    "type",
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
    package: str | None = Field(default=None, max_length=255)
    direction: Literal["up", "down", "left", "right"] | None = None
    distance: float | None = Field(default=None, ge=0.1, le=1.0)
    seconds: float | None = Field(default=None, ge=0.2, le=5.0)
    message: str = Field(default="", max_length=1000)
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _require_action_arguments(self) -> "Action":
        if self.type == "tap" and not (
            self.target is not None
            or (self.element_text or "").strip()
            or (self.x is not None and self.y is not None)
        ):
            raise ValueError("tap needs an element index, an element description or x/y")
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
    "long_press": "tap",
    "input": "type",
    "type": "type",
    "input_text": "type",
    "enter_text": "type",
    "scroll": "scroll",
    "swipe": "scroll",
    "drag": "scroll",
    "open_app": "open_app",
    "launch": "open_app",
    "launch_app": "open_app",
    "navigate": "open_app",
    "back": "back",
    "go_back": "back",
    "home": "home",
    "go_home": "home",
    "recents": "recents",
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
        text = (raw or "").strip()
        if not text:
            raise ActionParseError("model returned an empty action")
        fenced = _FENCED_JSON_RE.search(text)
        if fenced:
            text = fenced.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            # Phone-use models sometimes stop one character early or leave a
            # stray quote. Repair the obvious cases instead of failing the
            # whole phone command.
            candidates = (
                text,
                text + "}",
                text.rstrip('"') + "}",
                text.rstrip('",') + "}",
                text + '"}',
                text.rstrip('"') + '"}',
                text.rstrip('"') + "]}",
            )
            for candidate in candidates:
                try:
                    payload = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue
            else:
                raise ActionParseError(f"model did not return JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ActionParseError("action must be a JSON object")

    try:
        return Action.model_validate(_normalize_action(payload))
    except ValidationError as exc:
        raise ActionParseError(f"invalid action: {exc}") from exc


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

    # The element the model wants, described in words.
    for key in ("action_ui_element", "ui_element", "element", "element_description", "target_description"):
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

    # mobile-use emits 0..1 fractions, our protocol is 0..1000.
    x, y = out.get("x"), out.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        if 0 <= float(x) <= 1 and 0 <= float(y) <= 1:
            out["x"] = int(round(float(x) * 1000))
            out["y"] = int(round(float(y) * 1000))
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
    if out.get("type") == "type" and not out.get("text"):
        for key in ("input_text", "text_to_type", "value"):
            value = out.get(key)
            if isinstance(value, str) and value.strip():
                out["text"] = value.strip()
                break

    out.pop("action_type", None)
    out.pop("action_ui_element", None)
    out.pop("action", None)
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
