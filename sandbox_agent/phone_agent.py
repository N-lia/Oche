"""The OYI phone-use agent, as it runs inside a Nebius Sandbox.

This program is uploaded into a sandbox operation and executed there. It owns
the *decision loop* for one phone command:

    observation  ->  model call  ->  raw action  ->  gateway  ->  phone
         ^                                                      |
         +------------------------------------------------------+

The gateway supplies the fully rendered prompt (so the prompt lives in one
place) and validates the action, so the sandbox only has to speak to the
model and relay the result.  Everything here is standard library: the sandbox
image needs no packages.

Environment (set by the host):
    OYI_GATEWAY_URL     base URL of the OYI gateway
    OYI_GATEWAY_TOKEN   bearer token for the gateway
    OYI_JOB_ID          the job this run drives
    OYI_VISION_MODEL    model id, e.g. nvidia/Nemotron-3.5-Super-VL
    NEBIUS_API_KEY      token factory key
    NEBIUS_BASE_URL     optional, defaults to the public inference endpoint
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

GATEWAY = os.environ["OYI_GATEWAY_URL"].rstrip("/")
TOKEN = os.environ["OYI_GATEWAY_TOKEN"]
JOB_ID = os.environ["OYI_JOB_ID"]
MODEL = os.environ.get("OYI_VISION_MODEL", "nvidia/Nemotron-3.5-Super-VL")
API_KEY = os.environ["NEBIUS_API_KEY"]
BASE_URL = os.environ.get(
    "NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"
).rstrip("/")

MAX_STEPS = int(os.environ.get("OYI_MAX_STEPS", "16"))


def _request(url: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body else {}


def gateway_get(path: str) -> dict:
    return _request(f"{GATEWAY}{path}", timeout=180.0)


def gateway_post(path: str, payload: dict) -> dict:
    return _request(f"{GATEWAY}{path}", payload=payload, timeout=120.0)


def call_model(messages: list[dict]) -> str:
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 700,
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120.0) as response:
        data = json.loads(response.read())
    choices = data.get("choices") or []
    if not choices:
        return ""
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content or ""


def main() -> int:
    for step in range(MAX_STEPS):
        try:
            observation = gateway_get(f"/api/device/job/{JOB_ID}/observation")
        except urllib.error.URLError as error:
            print(f"observation failed: {error}", file=sys.stderr)
            return 1

        if observation.get("finished"):
            gateway_post(
                f"/api/device/job/{JOB_ID}/result",
                {
                    "status": observation.get("status", "error"),
                    "message": observation.get("message", ""),
                },
            )
            return 0

        messages = observation.get("messages") or []
        try:
            raw = call_model(messages)
        except Exception as error:  # noqa: BLE001 - reported to the gateway
            print(f"model call failed: {error}", file=sys.stderr)
            gateway_post(
                f"/api/device/job/{JOB_ID}/result",
                {"status": "error", "message": f"Vision model call failed: {error}"},
            )
            return 1

        verdict = gateway_post(
            f"/api/device/job/{JOB_ID}/action",
            {"raw": raw, "step": step},
        )
        if verdict.get("finished"):
            gateway_post(
                f"/api/device/job/{JOB_ID}/result",
                {
                    "status": verdict.get("status", "completed"),
                    "message": verdict.get("message", ""),
                },
            )
            return 0

    gateway_post(
        f"/api/device/job/{JOB_ID}/result",
        {
            "status": "completed",
            "message": f"Stopped after the {MAX_STEPS}-step limit.",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
