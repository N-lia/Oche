#!/usr/bin/env python3
"""Tests for the live voice session's turn state machine and interruption.

Uses a fake ASR stream, a fake agent and a fake TTS so the turn-taking and
barge-in logic runs without any network or model.

Run:
    .venv/bin/python test_voice_interrupt.py
"""

from __future__ import annotations

import asyncio
import base64
import sys

from voice_session import SESSION_IDLE, SESSION_LISTENING, SESSION_RESPONDING, VoiceSession


class FakeStream:
    """Stand-in for AsyncTranscribeStream with a scriptable event queue."""

    def __init__(self) -> None:
        self.events: asyncio.Queue = asyncio.Queue()
        self.fed: list[bytes] = []
        self.finished = False
        self.closed = False

    def feed(self, pcm: bytes) -> None:
        self.fed.append(pcm)

    def finish(self) -> None:
        self.finished = True

    def close(self) -> None:
        self.closed = True

    async def _generate(self):
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    def __aiter__(self):
        return self._generate()


def _drain(queue: asyncio.Queue) -> list[dict]:
    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())
    return messages


def _types(messages: list[dict]) -> list[str]:
    return [m.get("type") for m in messages]


def _make_session(streams: list[FakeStream], agent, tts, state: dict) -> tuple[VoiceSession, asyncio.Queue]:
    queue: asyncio.Queue = asyncio.Queue()

    def factory(_sample_rate: int) -> FakeStream:
        stream = FakeStream()
        streams.append(stream)
        return stream

    session = VoiceSession(
        queue,
        agent_streamer=agent,
        tts=tts,
        stream_factory=factory,
        clip_exists=lambda _p: True,   # never trigger real clip synthesis
        clip_url=lambda p: f"/api/backchannels/{p}.mp3",
    )
    return session, queue


async def test_interrupt_cancels_reply_and_starts_new_turn() -> None:
    streams: list[FakeStream] = []
    state: dict = {"cancelled": False}

    async def agent(text, turn_id):
        yield f"reply to {text}"
        try:
            await asyncio.sleep(3600)  # hold the reply open to be interrupted
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def tts(text, mood):
        return b"MP3"

    session, queue = _make_session(streams, agent, tts, state)
    await session.on_control({"type": "start", "sample_rate": 16000})

    # Turn 1: audio begins it, flush finalizes, ASR emits <EOU>.
    await session.on_audio(b"\x00\x00" * 160)
    assert session.state == SESSION_LISTENING and session.turn_id == 1
    assert streams[0].fed, "audio must reach the ASR stream"
    await session.on_control({"type": "flush"})
    assert streams[0].finished, "flush must finish the ASR stream"
    await streams[0].events.put({"type": "eou", "text": "book a table", "model_eou": True})
    await asyncio.sleep(0.05)
    assert session.state == SESSION_RESPONDING, session.state
    assert session.response_task is not None

    # Barge in with a preroll of the interrupting audio.
    preroll_bytes = b"\x01\x02" * 8
    preroll_b64 = base64.b64encode(preroll_bytes).decode()
    await session.on_control({"type": "barge_in", "preroll": preroll_b64})

    assert state["cancelled"] is True, "the in-flight reply must be cancelled"
    assert session.turn_id == 2, "a new turn id must be issued"
    assert session.state == SESSION_LISTENING
    assert len(streams) == 2, "a fresh ASR stream must be opened"
    assert preroll_bytes in streams[1].fed, "preroll audio must seed the new turn"

    messages = _drain(queue)
    assert "interrupted" in _types(messages)
    interrupted = next(m for m in messages if m["type"] == "interrupted")
    assert interrupted["turn_id"] == 1, "interrupted should reference the old turn"


async def test_interrupt_before_eou_ignores_stale_turn() -> None:
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "should never run"

    async def tts(text, mood):
        return b"MP3"

    session, queue = _make_session(streams, agent, tts, {})
    await session.on_control({"type": "start"})
    await session.on_audio(b"\x03\x00" * 160)
    await session.on_control({"type": "flush"})
    assert session.state == "finalizing"

    # Barge in before the previous turn's <EOU> arrives.
    await session.on_control({"type": "barge_in"})
    assert session.turn_id == 2 and session.state == SESSION_LISTENING

    # The abandoned turn's late <EOU> must be ignored (turn_id mismatch).
    await streams[0].events.put({"type": "eou", "text": "stale", "model_eou": True})
    await asyncio.sleep(0.05)
    assert session.state == SESSION_LISTENING
    assert session.response_task is None
    assert "eou" not in _types(_drain(queue)), "stale eou must not surface"


async def test_normal_turn_completes_cleanly() -> None:
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "Hi"
        yield " there"

    async def tts(text, mood):
        return b"VOICE"

    session, queue = _make_session(streams, agent, tts, {})
    await session.on_control({"type": "start"})
    await session.on_audio(b"\x00\x00" * 160)
    await session.on_control({"type": "flush"})
    await streams[0].events.put({"type": "eou", "text": "hello", "model_eou": True})
    await asyncio.sleep(0.1)

    assert session.state == SESSION_IDLE
    assert session.response_task is None
    messages = _drain(queue)
    types = _types(messages)
    assert "eou" in types
    assert "bot_audio" in types
    assert types[-1] == "turn_end"
    audio = next(m for m in messages if m["type"] == "bot_audio")
    assert audio["turn_id"] == 1
    assert base64.b64decode(audio["audio"]) == b"VOICE"


async def test_barge_in_while_idle_is_noop() -> None:
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "unused"

    async def tts(text, mood):
        return b""

    session, queue = _make_session(streams, agent, tts, {})
    await session.on_control({"type": "start"})
    _drain(queue)  # discard the ready handshake
    await session.on_control({"type": "barge_in"})
    assert session.turn_id == 0, "a barge-in with nothing pending must not start a turn"
    assert _drain(queue) == []


async def test_model_eou_closes_the_stream() -> None:
    """A turn ending on model <EOU> (no client flush) must close its stream.

    Regression: it used to stay open, pinning a thread in the ASR server's
    worker pool. Two leaked streams wedged the whole ASR service.
    """
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "ok"

    async def tts(text, mood):
        return b""

    session, queue = _make_session(streams, agent, tts, {})
    await session.on_control({"type": "start"})
    await session.on_audio(b"\x00\x00" * 160)
    assert session.stream is streams[0]

    # <EOU> arrives straight from the model, without a client flush.
    await streams[0].events.put({"type": "eou", "text": "hi", "model_eou": True})
    await asyncio.sleep(0.1)

    assert streams[0].closed, "stream must be closed when the turn ends on <EOU>"
    assert session.stream is None, "no stream may be left dangling"

    # A following turn must not orphan the previous stream.
    await session.on_audio(b"\x00\x00" * 160)
    assert len(streams) == 2, "a fresh stream must be opened for the new turn"
    assert session.stream is streams[1]
    await session.aclose()
    assert streams[1].closed


async def test_aclose_releases_a_listening_stream() -> None:
    """Disconnecting mid-turn must terminate the stream (no worker leak)."""
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "unused"

    async def tts(text, mood):
        return b""

    session, queue = _make_session(streams, agent, tts, {})
    await session.on_control({"type": "start"})
    await session.on_audio(b"\x00\x00" * 160)
    assert session.stream is streams[0] and not streams[0].closed

    await session.aclose()

    assert streams[0].closed, "aclose must terminate the listening stream"
    assert session.stream is None
    assert session.consumer is None


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
