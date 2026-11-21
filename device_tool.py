"""Agent tool for controlling the paired OYI Android phone."""

from __future__ import annotations

import logging
import os

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("oyi.device.tool")


class ControlPhoneInput(BaseModel):
    request: str = Field(
        description=(
            "Natural-language task to perform on the phone, e.g. 'open Settings and "
            "turn on dark mode' or 'what app is open right now?'."
        )
    )
    device_id: str = Field(
        default="",
        description="Optional paired device id. Leave empty when only one phone is online.",
    )
    max_steps: int = Field(
        default=12,
        ge=1,
        le=24,
        description="Maximum UI actions for this task. Keep small for simple tasks.",
    )


class PhoneStatusInput(BaseModel):
    device_id: str = Field(
        default="",
        description="Optional paired device id. Leave empty when only one phone is online.",
    )


def _gateway_url() -> str:
    return os.getenv("OYI_DEVICE_GATEWAY_URL", "").strip().rstrip("/")


def _api_token() -> str:
    return (
        os.getenv("OYI_DEVICE_API_TOKEN", "").strip()
        or os.getenv("OYI_DEVICE_TOKEN", "").strip()
    )


@tool(args_schema=ControlPhoneInput)
async def control_phone(request: str, device_id: str = "", max_steps: int = 12) -> str:
    """Control Sir Mario's paired Android phone with a vision-guided agent.

    Use ONLY when he explicitly asks to do something on his phone or asks what
    is happening on the phone. The phone takes a screenshot and accessibility
    tree for each step; nvidia/Nemotron-3.5-Super-VL chooses one action at a
    time. Do not use for ordinary chat, web research, or PC/file tasks.

    The tool waits for the phone command to finish. If it times out, report that
    clearly rather than inventing a result. Never use it to enter passwords,
    bypass locks, or perform destructive actions the user did not explicitly
    request.
    """
    base_url = _gateway_url()
    token = _api_token()
    if not base_url or not token:
        return (
            "Phone control is not configured. Set OYI_DEVICE_GATEWAY_URL and "
            "OYI_DEVICE_API_TOKEN in OYI's environment."
        )
    if not request.strip():
        return "Phone control needs a concrete request."

    timeout_s = min(260.0, max(30.0, 25.0 * max_steps))
    payload = {
        "text": request.strip(),
        "device_id": device_id.strip(),
        "max_steps": max_steps,
        "timeout_s": timeout_s,
        "thread_id": "phone-tool",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=timeout_s + 20.0, write=20.0, pool=10.0)
        ) as client:
            response = await client.post(
                f"{base_url}/api/device/command",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        LOGGER.warning("Device gateway returned %s: %s", exc.response.status_code, exc.response.text[:500])
        return f"Phone control failed ({exc.response.status_code}): {_short(exc.response.text)}"
    except Exception as exc:  # noqa: BLE001 - returned to the agent
        LOGGER.exception("Device gateway request failed")
        return f"Phone control failed: {exc}"

    status = str(data.get("status", "error"))
    message = str(data.get("message", "")).strip()
    steps = data.get("steps", 0)
    if status == "completed":
        return message or f"Phone task completed in {steps} step(s)."
    if message:
        return f"Phone task {status} after {steps} step(s): {message}"
    return f"Phone task {status} after {steps} step(s)."


def _short(text: str) -> str:
    text = text.strip().replace("\n", " ")
    return text[:300]


@tool(args_schema=PhoneStatusInput)
async def phone_status(device_id: str = "") -> str:
    """Report what Sir Mario's phone is doing right now.

    Use this when he asks which app is open, how long he has been in it, or how
    much screen time he has used today. It reads the phone's local usage data
    and does not take any action on the screen.
    """
    base_url = _gateway_url()
    token = _api_token()
    if not base_url or not token:
        return (
            "Phone status is not configured. Set OYI_DEVICE_GATEWAY_URL and "
            "OYI_DEVICE_API_TOKEN in OYI's environment."
        )

    try:
        params: dict[str, str] = {}
        wanted = device_id.strip()
        if wanted and wanted != "default":
            params["device_id"] = wanted
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)
        ) as client:
            response = await client.get(
                f"{base_url}/api/device/status",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        return f"Phone status unavailable ({exc.response.status_code}): {_short(exc.response.text)}"
    except Exception as exc:  # noqa: BLE001 - returned to the agent
        LOGGER.exception("Phone status request failed")
        return f"Phone status unavailable: {exc}"

    app_label = str(data.get("app_label") or data.get("package") or "unknown app")
    elapsed = _format_duration(int(data.get("elapsed_ms") or 0))
    today = _format_duration(int(data.get("today_ms") or 0))
    lines = [
        f"Current app: {app_label} ({elapsed} so far, {today} today).",
    ]

    top_apps = data.get("top_apps") or []
    if top_apps:
        lines.append("Today's top apps:")
        for entry in top_apps[:5]:
            label = str(entry.get("label") or entry.get("package") or "app")
            lines.append(f"- {label}: {_format_duration(int(entry.get('ms') or 0))}")
    if not data.get("screen_on", True):
        lines.append("The phone screen is currently off.")
    return "\n".join(lines)


def _format_duration(millis: int) -> str:
    minutes = millis // 60_000
    hours, rest = divmod(minutes, 60)
    if hours:
        return f"{hours}h {rest}m"
    if minutes:
        return f"{minutes}m"
    return "under a minute"


DEVICE_TOOLS = [control_phone, phone_status]
