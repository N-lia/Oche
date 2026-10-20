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

AUDIO = b"\x00\x00" * 160


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


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _drain(queue: asyncio.Queue) -> list[dict]:
    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())
    return messages


def _types(messages: list[dict]) -> list[str]:
    return [m.get("type") for m in messages]


def _make_session(streams: list[FakeStream], agent, tts) -> tuple[VoiceSession, asyncio.Queue]:
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


async def _user_turn(session: VoiceSession, streams: list[FakeStream], text: str) -> None:
    """Open a turn the way the client VAD does, feed audio, then finalize."""
    await session.on_control({"type": "speech_start", "preroll": _b64(AUDIO)})
    await session.on_audio(AUDIO)
    await session.on_control({"type": "speech_end"})
    await streams[-1].events.put({"type": "eou", "text": text, "model_eou": True})
    await asyncio.sleep(0.05)


async def test_audio_outside_a_turn_does_not_open_one() -> None:
    """Noise and speaker echo must never start a turn: the VAD owns boundaries."""
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "unused"

    async def tts(text, mood):
        return b""

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    await session.on_audio(AUDIO)
    await asyncio.sleep(0.02)

    assert session.state == SESSION_IDLE
    assert session.turn_id == 0
    assert streams == [], "audio outside a turn must not open an ASR stream"
    await session.aclose()


async def test_barge_in_ducks_then_cancels_on_a_real_turn() -> None:
    """Barge-in must not kill the reply until the transcript proves it is a turn."""
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

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start", "sample_rate": 16000})

    # Turn 1: the VAD opens it, audio feeds it, speech_end finalizes.
    await session.on_control({"type": "speech_start", "preroll": _b64(AUDIO)})
    assert session.state == SESSION_LISTENING and session.turn_id == 1
    await session.on_audio(AUDIO)
    assert streams[0].fed, "audio must reach the ASR stream"
    await session.on_control({"type": "speech_end"})
    assert streams[0].finished, "speech_end must finish the ASR stream"
    await streams[0].events.put({"type": "eou", "text": "book a table", "model_eou": True})
    await asyncio.sleep(0.05)
    assert session.state == SESSION_RESPONDING, session.state
    assert session.response_task is not None
    _drain(queue)

    # Barge in with a preroll of the interrupting audio.
    preroll_bytes = b"\x01\x02" * 8
    await session.on_control({"type": "barge_in", "preroll": _b64(preroll_bytes)})

    # Soft barge: the reply is ducked but still alive until we know what was said.
    assert state["cancelled"] is False, "a bare barge-in must not cancel the reply"
    assert session.turn_id == 2, "a new turn id must be issued"
    assert session.state == SESSION_LISTENING
    assert len(streams) == 2, "a fresh ASR stream must be opened"
    assert preroll_bytes in streams[1].fed, "preroll audio must seed the new turn"
    assert "barge_pending" in _types(_drain(queue))

    # The transcript is a real request: now the old reply is cancelled.
    await streams[1].events.put({"type": "eou", "text": "make it for two", "model_eou": True})
    await asyncio.sleep(0.05)
    assert state["cancelled"] is True, "a real turn must cancel the ducked reply"
    messages = _drain(queue)
    assert "interrupted" in _types(messages)
    interrupted = next(m for m in messages if m["type"] == "interrupted")
    assert interrupted["turn_id"] == 1, "interrupted should reference the old turn"
    await session.aclose()


async def test_barge_in_with_a_continuer_resumes_the_reply() -> None:
    """\"mm-hmm\"/\"ok\" over the agent is a continuer, not an interruption."""
    streams: list[FakeStream] = []
    state: dict = {"cancelled": False}

    async def agent(text, turn_id):
        yield "Sure, working on it"
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def tts(text, mood):
        return b"MP3"

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    await _user_turn(session, streams, "plan my day")
    assert session.state == SESSION_RESPONDING
    _drain(queue)  # discard turn 1's handshake/status events

    await session.on_control({"type": "barge_in", "preroll": _b64(b"\x01\x02" * 8)})
    await streams[1].events.put({"type": "eou", "text": "hmm", "model_eou": True})
    await asyncio.sleep(0.05)

    assert state["cancelled"] is False, "a continuer must not cancel the reply"
    assert session.state == SESSION_RESPONDING, "the ducked reply resumes"
    messages = _drain(queue)
    continuation = next(m for m in messages if m["type"] == "continuation")
    assert continuation["text"] == "hmm"
    eou = next(m for m in messages if m["type"] == "eou")
    assert eou["verdict"] == "continuation"
    await session.aclose()
    assert state["cancelled"] is True, "aclose must still release the reply task"


async def test_audio_held_during_a_barge_resumes_after_a_continuation() -> None:
    """Audio that is ready mid-barge must wait, then play if it was only "hmm"."""
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "Sure thing"

    async def tts(text, mood):
        await asyncio.sleep(0.15)
        return b"VOICE"

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    await _user_turn(session, streams, "plan my day")
    _drain(queue)

    # TTS is still in flight when the user makes a noise.
    await session.on_control({"type": "barge_in", "preroll": _b64(b"\x01\x02" * 8)})
    await asyncio.sleep(0.25)
    types = _types(_drain(queue))
    assert "bot_audio" not in types, "must not talk over the user's utterance"

    await streams[1].events.put({"type": "eou", "text": "hmm", "model_eou": True})
    await asyncio.sleep(0.05)
    messages = _drain(queue)
    audio = [m for m in messages if m["type"] == "bot_audio"]
    assert audio, "the held reply must play after a continuer"
    assert audio[0]["turn_id"] == 1
    assert base64.b64decode(audio[0]["audio"]) == b"VOICE"
    await session.aclose()


async def test_continuation_never_reaches_the_agent() -> None:
    streams: list[FakeStream] = []
    calls: list[str] = []

    async def agent(text, turn_id):
        calls.append(text)
        yield "unused"

    async def tts(text, mood):
        return b""

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    _drain(queue)
    await _user_turn(session, streams, "okay")

    assert calls == [], "an acknowledgement must not invoke the agent"
    messages = _drain(queue)
    assert "continuation" in _types(messages)
    assert _types(messages)[-1] == "turn_end"
    assert session.state == SESSION_IDLE
    await session.aclose()


async def test_ack_answers_a_question() -> None:
    """After a question, \"ok\" is an answer, not a continuer."""
    streams: list[FakeStream] = []
    calls: list[str] = []

    async def agent(text, turn_id):
        calls.append(text)
        yield "Shall I book it?"

    async def tts(text, mood):
        return b"VOICE"

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    await _user_turn(session, streams, "book a table")
    await asyncio.sleep(0.05)
    assert session.expecting_answer is True
    assert session.state == SESSION_IDLE

    await _user_turn(session, streams, "ok")
    await asyncio.sleep(0.05)

    assert calls == ["book a table", "ok"], calls
    await session.aclose()


async def test_interrupt_before_eou_ignores_stale_turn() -> None:
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "should never run"

    async def tts(text, mood):
        return b"MP3"

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    await session.on_control({"type": "speech_start", "preroll": _b64(AUDIO)})
    await session.on_control({"type": "speech_end"})
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
    await session.aclose()


async def test_normal_turn_completes_cleanly() -> None:
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "Hi"
        yield " there"

    async def tts(text, mood):
        return b"VOICE"

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    await _user_turn(session, streams, "hello")
    await asyncio.sleep(0.05)

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
    await session.aclose()


async def test_barge_in_while_idle_is_noop() -> None:
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "unused"

    async def tts(text, mood):
        return b""

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    _drain(queue)  # discard the ready handshake
    await session.on_control({"type": "barge_in"})
    assert session.turn_id == 0, "a barge-in with nothing pending must not start a turn"
    assert _drain(queue) == []
    await session.aclose()


async def test_model_eou_closes_the_stream() -> None:
    """A turn ending on <EOU> must close its stream.

    Regression: it used to stay open, pinning a thread in the ASR server's
    worker pool. Two leaked streams wedged the whole ASR service.
    """
    streams: list[FakeStream] = []

    async def agent(text, turn_id):
        yield "ok"

    async def tts(text, mood):
        return b""

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    await session.on_control({"type": "speech_start", "preroll": _b64(AUDIO)})
    assert session.stream is streams[0]

    # <EOU> arrives from the ASR as the turn finalizes.
    await streams[0].events.put({"type": "eou", "text": "hi", "model_eou": True})
    await asyncio.sleep(0.1)

    assert streams[0].closed, "stream must be closed when the turn ends on <EOU>"
    assert session.stream is None, "no stream may be left dangling"

    # A following turn must not orphan the previous stream.
    await session.on_control({"type": "speech_start", "preroll": _b64(AUDIO)})
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

    session, queue = _make_session(streams, agent, tts)
    await session.on_control({"type": "start"})
    await session.on_control({"type": "speech_start", "preroll": _b64(AUDIO)})
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
