"""Local Phonon TTS for the livechat voice path (in-process on the Oracle VM).

``ptts`` 0.4.0rc3 with the ``gradium/phonon-31m-en`` q8 checkpoint (64 MB,
24 kHz) loads in ~0.1 s and synthesizes at ~0.2x realtime on the VM's 4
Ampere cores - no Modal GPU round trip, no MP3 encode, WAV out.

Prosody: this checkpoint exposes only ``num_speakers`` and
``duration_delta`` conditioners (no emotion/style ones exist). We map the
session mood onto ``duration_delta`` pacing: negative values speak faster,
positive slower (measured: -0.5 -> ~28% faster, +0.5 -> ~28% slower).

Sizing discipline: long text is split into sentence-sized chunks so a whole
turn always finishes at MAP speed; the engine call itself is serialized by a
lock because the runtime is single-threaded.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import threading
import wave
from collections.abc import AsyncIterator

LOGGER = logging.getLogger("oyi.phonon")

# The checkpoint ships on the VM at image build time (see Dockerfile.webapp);
# it is deliberately not committed to git. Read at call time so tests and
# containers can point it wherever the files live.

# Threads the GGUF runtime may use; the VM only has 4 cores shared with the
# agent and the ASR/turn-scoring work.
_DEFAULT_THREADS = int(os.environ.get("PHONON_THREADS", "2"))

# Mood -> duration_delta seconds. Negative = faster, positive = slower.
_MOOD_PACING = {
    "normal": 0.0,
    "excited": -0.35,
    "energetic": -0.35,
    "urgent": -0.4,
    "happy": -0.2,
    "calm": 0.25,
    "serious": 0.15,
    "sleepy": 0.4,
}

# Sentence groups are kept small: phonon synthesizes a full sentence in
# ~0.2-1.5s of wall time, and one turn must finish while the user listens.
_MAX_CHUNK_CHARS = 220

# The opening chunk is deliberately much smaller: time-to-first-audio is
# dominated by how much text the engine must synthesize before the first frame
# can ship. 80 chars ~= 4.5s of speech ~= 1.1s of synthesis on the VM, and
# halves the base64 payload of the first WebSocket frame.
_FIRST_CHUNK_CHARS = 80

_ENGINE = None
_ENGINE_LOCK = threading.Lock()
_SYNTH_LOCK = threading.Lock()  # phonon runtime is not thread-safe


def mood_pacing(mood: str | None) -> float:
    """Map a session mood to the speaker's duration_delta pacing value."""
    return _MOOD_PACING.get((mood or "").strip().lower(), 0.0)


def _get_engine():
    """Load the engine once and reuse it for every turn."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        import ptts

        path = os.environ.get("PHONON_MODEL_DIR", "/opt/phonon").rstrip("/")
        required = ("model.q8.gguf", "tokenizer.json", "default-voice.safetensors", "config.json")
        missing = [name for name in required if not os.path.exists(os.path.join(path, name))]
        if missing:
            raise RuntimeError(
                f"Phonon checkpoint incomplete at {path} (missing: {', '.join(missing)}). "
                "Copy model.q8.gguf/config.json/tokenizer.json/default-voice.safetensors "
                "from gradium/phonon-31m-en @ 52433c469."
            )
        try:
            ptts.set_num_threads(max(1, min(_DEFAULT_THREADS, 8)))
        except Exception:  # noqa: BLE001
            LOGGER.debug("ptts.set_num_threads unavailable", exc_info=True)
        engine = ptts.TTS(path, lang="en", quant="q8")
        _ENGINE = engine
        LOGGER.info("Phonon engine loaded from %s (threads=%d, sample_rate=%d)", path, _DEFAULT_THREADS, engine.sample_rate)
        return engine


_SENTENCE_RE = re.compile(r"[^.!?\u2026]+[.!?\u2026]*\s*|[^.!?\u2026]+$")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;:\u2014])\s+")


def _sentence_units(text: str) -> list[str]:
    units: list[str] = []
    for part in _SENTENCE_RE.findall(text):
        stripped = part.strip()
        if stripped:
            units.append(stripped)
    return units


def _split_sentences(text: str) -> list[str]:
    """Greedy sentence packing so every chunk stays below the size budget.

    Whole-file syntheses want FEW chunks (one audio blob), so two short
    sentences merge into a block; the streaming path uses its own splitter.
    """
    if not text:
        return []
    units = _sentence_units(text)
    if not units:
        return [text.strip()]
    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(current) + len(unit) + 1 <= _MAX_CHUNK_CHARS:
            current = f"{current} {unit}".strip()
        else:
            if current:
                chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks or [text.strip()]


def _split_first(unit: str, limit: int) -> tuple[str, str]:
    """Split the opening sentence at a natural boundary near ``limit``.

    The first frame sets time-to-first-audio, so it is worth cutting a long
    opening sentence before its terminal punctuation. Prefer a clause boundary
    (comma, semicolon, colon, dash), then a word boundary, then hard-cut.
    """
    if len(unit) <= limit:
        return unit, ""
    window = unit[: limit + 1]
    for separator in (", ", "; ", ": ", " \u2014 ", " - "):
        cut = window.rfind(separator)
        if cut >= 20:
            end = cut + len(separator)
            return unit[:end].strip(), unit[end:].strip()
    cut = window.rfind(" ")
    if cut >= 20:
        return unit[:cut].strip(), unit[cut + 1 :].strip()
    return unit[:limit].strip(), unit[limit:].strip()


def _stream_split(text: str) -> list[str]:
    """Sentence-per-frame split for streaming.

    No packing (frames should be small to ship early), but long sentences
    without a terminal mark are sub-split at clause boundaries so a single
    frame can never push the engine past the size budget. The opening chunk is
    additionally capped at ``_FIRST_CHUNK_CHARS``: synthesizing (and shipping)
    80 chars takes roughly a quarter of the time of a full sentence, so the
    user hears the first words much sooner.
    """
    if not text:
        return []
    units = _sentence_units(text)
    if not units:
        return [text.strip()]
    first, tail = _split_first(units[0], _FIRST_CHUNK_CHARS)
    units = [first] + ([tail] if tail else []) + units[1:]
    chunks: list[str] = []
    for unit in units:
        if len(unit) <= _MAX_CHUNK_CHARS:
            chunks.append(unit)
            continue
        buffer = ""
        for piece in _CLAUSE_SPLIT_RE.split(unit):
            if len(buffer) + len(piece) + 1 <= _MAX_CHUNK_CHARS:
                buffer = f"{buffer} {piece}".strip()
            else:
                if buffer:
                    chunks.append(buffer)
                buffer = piece
        if buffer:
            chunks.append(buffer)
    return chunks


def _synthesize_block(engine, text: str, pacing: float) -> bytes:
    """Synthesize one text block to WAV bytes (16-bit PCM mono 24 kHz)."""
    with _SYNTH_LOCK:
        # Always set: conditions persist inside the engine, so relying on a
        # skip-when-zero would leak the previous turn's pacing into this one.
        engine.set_conditions({"duration_delta": pacing})
        audio = engine.synth(text)
    import numpy as np

    data = np.asarray(audio, dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(data))) if data.size else 1.0
    if peak > 0:
        data = data * (0.95 / peak)
    # Edge fades kill chunk-boundary clicks without touching the body.
    fade = min(data.size // 4, 288)  # ~12 ms at 24 kHz
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        data[:fade] *= ramp
        data[-fade:] *= ramp[::-1]
    return _pcm16_wav(data, engine.sample_rate)


def _pcm16_wav(data, sample_rate: int) -> bytes:
    import numpy as np

    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


async def synthesize_phonon_wav(
    text: str,
    mood: str = "normal",
    event: str = "chat",
    *,
    feeding: float | None = None,
    **dials,
) -> bytes:
    """Synthesize ``text`` locally and return WAV bytes (24 kHz mono PCM16)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Phonon TTS got empty text")

    pacing = feeding if feeding is not None else mood_pacing(mood)
    engine = await asyncio.to_thread(_get_engine)
    chunks = _split_sentences(text)
    if len(chunks) > 1:
        LOGGER.info("Phonon: synthesizing %d chunks (mood=%s pacing=%s)", len(chunks), mood, pacing)
    # Sequential: the single engine serializes anyway (it is not thread-safe);
    # keeping one to_thread at a time preserves NV ordering without lock queues.
    wav_parts = []
    for chunk in chunks:
        wav_parts.append(await asyncio.to_thread(_synthesize_block, engine, chunk, pacing))
    if len(wav_parts) == 1:
        return wav_parts[0]

    # Multi-chunk: decode the WAV payloads back to PCM and concatenate into
    # one continuous file (keeps the client on a single bot_audio blob).
    import numpy as np
    import wave as _wave

    frames: list[bytes] = []
    rate = None
    for part in wav_parts:
        with _wave.open(io.BytesIO(part), "rb") as w:
            rate = w.getframerate()
            frames.append(w.readframes(w.getnframes()))
    return _pcm16_wav(np.concatenate([
        np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0 for frame in frames
    ]), rate)


def plan_chunks(text: str) -> list[str]:
    """The exact sentence chunks :func:`stream_phonon_wav` will yield.

    Streaming consumers need the frame count (and each chunk's text) before
    the first chunk finishes synthesizing, so the split is exposed as a
    deterministic standalone function.
    """
    return _stream_split((text or "").strip())


async def stream_phonon_wav(
    text: str,
    mood: str = "normal",
    event: str = "chat",
    *,
    feeding: float | None = None,
    **dials,
) -> AsyncIterator[bytes]:
    """Async generator: yield one WAV per sentence chunk, first chunk first.

    modal-phonon-voice-bot's producer->queue->frames shape, with this
    generator as the producer: a caller can send the first sentence while
    the rest of the reply is still synthesizing, which is what makes
    time-to-first-audio independent of reply length.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Phonon TTS got empty text")

    pacing = feeding if feeding is not None else mood_pacing(mood)
    engine = await asyncio.to_thread(_get_engine)
    # Same split as plan_chunks: the generator and the planner always agree
    # on both order and count.
    for chunk in _stream_split(text):
        yield await asyncio.to_thread(_synthesize_block, engine, chunk, pacing)


def is_wav(payload: bytes) -> bool:
    """True when a TTS payload is a RIFF/WAVE file (vs Modal's MP3)."""
    return bool(payload) and payload[:4] == b"RIFF"
