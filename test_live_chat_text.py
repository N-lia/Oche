#!/usr/bin/env python3
"""Tests for the WebApp text/activity turn path in VoiceSession.

Uses a scripted event streamer so the structured status/tool/token handling
runs without a model or network.

Run:
    .venv/bin/python test_live_chat_text.py
"""

from __future__ import annotations

import asyncio
import sys

from voice_session import SESSION_IDLE, SESSION_RESPONDING, VoiceSession


def _drain(queue: asyncio.Queue) -> list[dict]:
    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())
    return messages


def _types(messages: list[dict]) -> list[str]:
    return [m.get("type") for m in messages]


def _make_session(events: list[dict], state: dict) -> tuple[VoiceSession, asyncio.Queue]:
    queue: asyncio.Queue = asyncio.Queue()

    async def streamer(text, turn_id):
        for event in events:
            yield dict(event)
            await asyncio.sleep(0)

    async def tts(text, mood):
        state["tts_text"] = text
        return b"MP3"

    session = VoiceSession(
        queue,
        event_streamer=streamer,
        tts=tts,
        clip_exists=lambda _p: True,
        clip_url=lambda p: f"/api/backchannels/{p}.mp3",
    )
    return session, queue


async def test_text_turn_streams_activity_and_tokens() -> None:
    events = [
        {"type": "status", "phase": "thinking", "text": "Thinking…"},
        {"type": "tool", "name": "tavily_search", "label": "Searching the web", "state": "start"},
        {"type": "tool", "name": "tavily_search", "label": "Searching the web", "state": "done"},
        {"type": "token", "text": "Hello "},
        {"type": "token", "text": "there."},
        {"type": "done"},
    ]
    state: dict = {}
    session, queue = _make_session(events, state)
    await session.on_control({"type": "start", "thread_id": "tg:42"})
    _drain(queue)  # drop the ready handshake

    await session.on_control({"type": "text", "text": "hi", "speak": False})
    assert session.state == SESSION_RESPONDING
    await asyncio.sleep(0.1)

    messages = _drain(queue)
    types = _types(messages)
    assert "user_text" in types
    assert "status" in types
    assert types.count("tool") == 2
    assert "bot_text_delta" in types
    assert types[-1] == "turn_end"
    assert session.state == SESSION_IDLE
    # speak=False must not synthesise audio.
    assert "bot_audio" not in types
    assert state.get("tts_text") is None

    reply = "".join(m["text"] for m in messages if m["type"] == "bot_text_delta")
    assert reply == "Hello there.", reply


async def test_text_turn_can_speak() -> None:
    events = [{"type": "token", "text": "spoken"}, {"type": "done"}]
    state: dict = {}
    session, queue = _make_session(events, state)
    await session.on_control({"type": "start"})
    _drain(queue)

    await session.on_control({"type": "text", "text": "say it", "speak": True})
    await asyncio.sleep(0.1)

    messages = _drain(queue)
    assert "bot_audio" in _types(messages)
    assert state.get("tts_text") == "spoken"


async def test_new_text_turn_supersedes_pending_reply() -> None:
    started = asyncio.Event()
    released = asyncio.Event()
    cancelled = {"value": False}

    async def streamer(text, turn_id):
        yield {"type": "token", "text": "first"}
        started.set()
        try:
            await released.wait()
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise

    queue: asyncio.Queue = asyncio.Queue()
    session = VoiceSession(
        queue,
        event_streamer=streamer,
        tts=lambda text, mood: _async_bytes(b"MP3"),
        clip_exists=lambda _p: True,
        clip_url=lambda p: p,
    )
    await session.on_control({"type": "start"})
    _drain(queue)

    await session.on_control({"type": "text", "text": "one", "speak": False})
    await asyncio.wait_for(started.wait(), timeout=1)
    first_turn = session.turn_id

    await session.on_control({"type": "text", "text": "two", "speak": False})
    assert session.turn_id == first_turn + 1
    assert cancelled["value"] is True, "the superseded reply must be cancelled"


async def _async_bytes(value: bytes) -> bytes:
    return value


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            asyncio.run(test())
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
