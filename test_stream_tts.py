#!/usr/bin/env python3
"""Tests for sentence-streamed TTS (phonon provider) in the voice session.

Run:
    .venv/bin/python test_stream_tts.py
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys

import voice_session as vs
from voice_session import VoiceSession

AUDIO = b"\x00\x00" * 160


class FakeStream:
    def __init__(self) -> None:
        self.events: asyncio.Queue = asyncio.Queue()
        self.fed: list[bytes] = []

    def feed(self, pcm: bytes) -> None:
        self.fed.append(pcm)

    def finish(self) -> None:
        ...

    def close(self) -> None:
        ...

    async def _generate(self):
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    def __aiter__(self):
        return self._generate()


def _b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode()


def _make_session(**kwargs) -> tuple[VoiceSession, asyncio.Queue, list[FakeStream]]:
    queue: asyncio.Queue = asyncio.Queue()
    reply = kwargs.pop("reply", "Hello there. Nice job.")

    async def agent(text, turn_id):
        yield reply

    async def tts(text, mood):
        return b""

    streams: list[FakeStream] = []

    def factory(_sample_rate: int) -> FakeStream:
        stream = FakeStream()
        streams.append(stream)
        return stream

    session = VoiceSession(
        queue,
        agent_streamer=agent,
        tts=tts,
        stream_factory=factory,
        clip_exists=lambda _p: True,
        clip_url=lambda p: f"/api/backchannels/{p}.mp3",
        **kwargs,
    )
    return session, queue, streams


async def _drain(queue: asyncio.Queue) -> list[dict]:
    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())
    return messages


async def _user_turn(session: VoiceSession, streams: list[FakeStream], text: str) -> None:
    await session.on_control({"type": "speech_start", "preroll": _b64(AUDIO)})
    await session.on_audio(AUDIO)
    await session.on_control({"type": "speech_end"})
    assert streams, "speech must have opened an ASR stream"
    await streams[-1].events.put({"type": "eou", "text": text, "model_eou": True})
    await asyncio.sleep(0.05)


def _patch_phonon_stream(session: VoiceSession):
    """Replace the phonon stream with a stub; return its restore callable."""
    async def fake_stream(text, mood="normal", event="chat", *, feeding=None, **kw):
        chunks = list(vs.plan_chunks(text))
        for chunk in chunks:
            yield b"RIFFwav" + chunk.encode()

    original = vs.stream_phonon_wav
    vs.stream_phonon_wav = fake_stream

    def restore() -> None:
        vs.stream_phonon_wav = original

    return restore


async def test_stream_one_frame_per_sentence() -> None:
    """Phonon replies arrive as ordered sentence frames with stream metadata."""
    os.environ["TTS_PROVIDER"] = "phonon"
    session, queue, streams = _make_session()
    restore = _patch_phonon_stream(session)
    try:
        await session.on_control({"type": "start"})
        await _user_turn(session, streams, "Give me two sentences")
        await asyncio.sleep(0.2)
        messages = await _drain(queue)
        frames = [m for m in messages if m.get("type") == "bot_audio"]
        assert len(frames) == 2, f"expected 2 sentence frames, got {len(frames)}"
        assert [f["frame"] for f in frames] == [0, 1]
        assert all(f["frames"] == 2 for f in frames)
        assert [f["final"] for f in frames] == [False, True]
        assert frames[0]["text"] == "Hello there."
        assert frames[1]["text"] == "Nice job."
        assert all(f["turn_id"] == frames[0]["turn_id"] for f in frames)
        assert any(m.get("type") == "turn_end" for m in messages)
        await session.aclose()
    finally:
        restore()
        os.environ.pop("TTS_PROVIDER", None)


async def test_stream_soft_barge_buffers_then_flushes() -> None:
    """Frames synthesized while the user talks buffer, then flush in order."""
    os.environ["TTS_PROVIDER"] = "phonon"
    session, queue, streams = _make_session()
    restore = _patch_phonon_stream(session)
    try:
        session.soft_barge = True
        session.reply_turn_id = 7
        await session._speak_stream("One. Two.", 7)
        messages = await _drain(queue)
        assert messages == [], "nothing may transmit while the user is talking"
        assert session.pending_frames is not None and len(session.pending_frames) == 2

        await session._flush_pending_audio()
        messages = await _drain(queue)
        frames = [m for m in messages if m.get("type") == "bot_audio"]
        assert [f["frame"] for f in frames] == [0, 1]
        assert [f["final"] for f in frames] == [False, True]
        assert session.pending_frames is None
        assert session.bot_speaking is True
        await session.aclose()
    finally:
        os.environ.pop("TTS_PROVIDER", None)


async def test_legacy_provider_sends_whole_file_with_frame_metadata() -> None:
    """Without phonon, the reply stays one bot_audio carrying final frame fields."""
    session, queue, streams = _make_session()
    await session._speak("Hello there.", 3)
    messages = await _drain(queue)
    frames = [m for m in messages if m.get("type") == "bot_audio"]
    assert len(frames) == 1
    frame = frames[0]
    assert frame["frame"] == 0 and frame["frames"] == 1 and frame["final"] is True
    assert frame["turn_id"] == 3
    assert frame["audio"] == _b64(b"")  # the fake tts produced empty bytes
    await session.aclose()


async def test_speak_empty_text_is_silent_noop() -> None:
    session, queue, streams = _make_session()
    os.environ["TTS_PROVIDER"] = "phonon"
    try:
        await session._speak("   \n", 5)
        assert queue.empty()
        await session.aclose()
    finally:
        os.environ.pop("TTS_PROVIDER", None)


def main() -> int:
    async def run_all() -> list[tuple[str, Exception | None]]:
        cases = [
            test_stream_one_frame_per_sentence,
            test_stream_soft_barge_buffers_then_flushes,
            test_legacy_provider_sends_whole_file_with_frame_metadata,
            test_speak_empty_text_is_silent_noop,
        ]
        results = []
        for case in cases:
            try:
                await case()
                results.append((case.__name__, None))
                print(f"\033[92m\u2713 {case.__name__}\033[0m")
            except Exception as exc:  # noqa: BLE001
                results.append((case.__name__, exc))
                print(f"\033[91m\u2717 {case.__name__}: {type(exc).__name__}: {exc}\033[0m")
        return results

    failures = asyncio.run(run_all())
    ok = sum(1 for _, err in failures if err is None)
    print(f"\n{ok}/{len(failures)} passed")
    return 0 if ok == len(failures) else 1


if __name__ == "__main__":
    sys.exit(main())
