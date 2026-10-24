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
    "tap_text",
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
    tree: list[dict[str, Any]] = Field(default_factory=list)
    screenshot: str = ""
    width: int = Field(default=0, ge=0, le=10_000)
    height: int = Field(default=0, ge=0, le=10_000)
    last_action_ok: bool = True
    error: str = Field(default="", max_length=1000)

    @field_validator("screenshot")
    @classmethod
    def _limit_screenshot(cls, value: str) -> str:
        if len(value) > _SCREENSHOT_LIMIT:
            raise ValueError("screenshot is too large")
        return value


class Action(BaseModel):
    """A single UI action chosen by the vision planner."""

    type: ActionType
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
        if self.type == "tap" and (self.x is None or self.y is None):
            raise ValueError("tap requires normalized x and y coordinates")
        if self.type in {"tap_text", "type"} and not (self.text or "").strip():
            raise ValueError(f"{self.type} requires text")
        if self.type == "open_app" and not (self.package or "").strip():
            raise ValueError("open_app requires a package")
        return self


class CommandRequest(BaseModel):
    """Request submitted by a channel adapter or the agent tool."""

    text: str = Field(min_length=1, max_length=4000)
    device_id: str = Field(default="", max_length=128)
    max_steps: int = Field(default=8, ge=1, le=16)
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
    """Parse a model response into a validated action.

    Vision models occasionally wrap JSON in a Markdown fence or emit an
    ``{"action": "tap", ...}`` envelope.  Both are accepted; the result is
    always an ``Action`` with a ``type`` field.
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
            raise ActionParseError(f"model did not return JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ActionParseError("action must be a JSON object")

    # Accept the common flat shape {"action": "tap", "x": ...}.
    if isinstance(payload.get("action"), str) and "type" not in payload:
        payload = {**payload, "type": payload["action"]}
        payload.pop("action", None)
    # Also accept {"action": {"type": "tap", ...}}.
    elif isinstance(payload.get("action"), dict):
        payload = payload["action"]

    try:
        return Action.model_validate(payload)
    except ValidationError as exc:
        raise ActionParseError(f"invalid action: {exc}") from exc


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
