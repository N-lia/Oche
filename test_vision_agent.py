#!/usr/bin/env python3
"""Tests for the Nebius vision planner.

Offline tests use a mocked httpx transport. The live test only runs when
OYI_LIVE_VISION=1 and NEBIUS_API_KEY is set.

Run:
    .venv/bin/python test_vision_agent.py
    OYI_LIVE_VISION=1 .venv/bin/python test_vision_agent.py
"""

from __future__ import annotations

import base64
import json
import os
import struct
import zlib

import httpx

from device_protocol import Observation
from vision_agent import DEFAULT_MODEL, VisionAgent, _image_mime


def _png(width: int = 8, height: int = 8, colour: tuple[int, int, int] = (10, 20, 30)) -> str:
    """Build a tiny valid PNG so the image payload is real data."""

    raw = bytearray()
    for _ in range(height):
        raw.append(0)
        raw += bytes(colour) * width

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


def _observation(screenshot: str = "") -> Observation:
    return Observation(
        command_id="cmd_test",
        step=0,
        package="com.android.settings",
        app_label="Settings",
        elements=[
            {
                "index": 1,
                "text": "Network & internet",
                "kind": "Text",
                "class": "android.widget.TextView",
                "id": "",
                "bounds": [0, 100, 720, 200],
            }
        ],
        element_text="[1] Text: 'Network & internet' | Bounds: [0,62][1000,125]",
        screenshot=screenshot,
        width=720,
        height=1600,
    )


def test_image_mime_detection() -> None:
    assert _image_mime(_png()) == "image/png"
    assert _image_mime(base64.b64encode(b"\xff\xd8\xff\xe0rest").decode()) == "image/jpeg"
    assert _image_mime("not-base64-at-all") == "image/jpeg"


def test_request_payload_and_parsing() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"type":"tap","target":1,"reason":"open it"}\n```'
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agent = VisionAgent(api_key="test-key", client=client)

    import asyncio

    action = asyncio.run(
        agent.next_action(
            instruction="Open Network & internet",
            observation=_observation(_png()),
            step=0,
        )
    )

    assert action.type == "tap"
    assert action.target == 1

    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer test-key"
    body = captured["json"]
    assert body["model"] == DEFAULT_MODEL
    user_content = body["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert "Open Network & internet" in user_content[0]["text"]
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert body["messages"][0]["role"] == "system"


def test_missing_api_key_is_reported() -> None:
    agent = VisionAgent(api_key="")
    import asyncio

    try:
        asyncio.run(
            agent.next_action(instruction="x", observation=_observation(), step=0)
        )
    except Exception as exc:  # noqa: BLE001 - asserted below
        assert "NEBIUS_API_KEY" in str(exc)
        return
    raise AssertionError("expected an error when NEBIUS_API_KEY is missing")


def test_live_nebius_roundtrip() -> None:
    if os.getenv("OYI_LIVE_VISION") != "1":
        print("  (live vision test skipped; set OYI_LIVE_VISION=1)")
        return
    from dotenv import load_dotenv

    load_dotenv("/home/mario/Documents/OYI/.env")
    if not os.getenv("NEBIUS_API_KEY"):
        print("  (live vision test skipped; NEBIUS_API_KEY not set)")
        return

    import asyncio

    agent = VisionAgent()

    async def run() -> object:
        try:
            return await agent.next_action(
                instruction="Tap the item labelled Network & internet",
                observation=_observation(_png()),
                step=0,
            )
        finally:
            await agent.aclose()

    action = asyncio.run(run())
    assert action.type in {
        "tap",
        "tap",
        "scroll",
        "type",
        "back",
        "home",
        "recents",
        "open_app",
        "wait",
        "done",
        "ask_user",
    }, action
    print(f"  live action: {action.model_dump(exclude_none=True)}")


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
    print("vision_agent tests passed")
