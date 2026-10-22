#!/usr/bin/env python3
"""Tests for the local Phonon TTS engine (livechat path).

Run:
    .venv/bin/python test_phonon_tts.py

The pacing test needs the real checkpoint (downloaded once into the HF
cache); the pure tests (mood map, chunking, wav helpers) run offline.
"""

from __future__ import annotations

import asyncio
import os
import sys
import wave
from pathlib import Path

os.environ.setdefault("NEBIUS_API_KEY", "test-key")

from phonon_tts import (
    _MAX_CHUNK_CHARS,
    mood_pacing,
    synthesize_phonon_wav,
)


def _checkpoint_dir() -> Path:
    from huggingface_hub import snapshot_download

    path = snapshot_download(
        "gradium/phonon-31m-en",
        revision="52433c46975bfa13144c56204c9f4ee1c5b148aa",
        allow_patterns=["config.json", "model.q8.gguf", "tokenizer.json", "default-voice.safetensors"],
    )
    return Path(path)


def _wav_duration(payload: bytes) -> float:
    with wave.open(__import__("io").BytesIO(payload), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# --------------------------------------------------------------- offline tests

def test_mood_pacing_map() -> None:
    assert mood_pacing("excited") < 0.0
    assert mood_pacing("EXCITED") == mood_pacing("excited")
    assert mood_pacing("calm") > 0.0
    assert mood_pacing("normal") == 0.0
    assert mood_pacing("totally-unknown-state") == 0.0


def test_faster_mood_paces_quicker() -> None:
    # The mood -> duration_delta mapping must go faster for the excited moods
    # than for the slow ones, in the same direction phonon documents.
    assert mood_pacing("urgent") < mood_pacing("normal") < mood_pacing("sleepy")


def test_split_sentences_sizes() -> None:
    from phonon_tts import _split_sentences

    long_text = (
        "The pipeline lives on my cabinet now. First audio in under a quarter "
        "second, and that is on CPU. " * 10
    )
    chunks = _split_sentences(long_text)
    assert chunks, "no chunks produced"
    assert all(len(chunk) <= _MAX_CHUNK_CHARS for chunk in chunks)
    # All content survives the chunking.
    joined = " ".join(chunks)
    assert "quarter second" in joined and "on CPU." in joined
    assert _split_sentences("") == [""]


def test_is_wav_helper() -> None:
    from voice_session import _audio_mime

    assert _audio_mime(b"RIFF\x00\x00\x00\x00") == "audio/wav"
    assert _audio_mime(b"ID3\x04\x00abc") == "audio/mpeg"
    assert _audio_mime(b"") == "audio/mpeg"


# --------------------------------------------------------------- engine tests

def test_synth_produces_valid_wav() -> None:
    model = _checkpoint_dir()
    os.environ["PHONON_MODEL_DIR"] = str(model)
    wav = asyncio.run(synthesize_phonon_wav("Hello Mario, this is the livechat voice.", mood="normal"))
    assert wav[:4] == b"RIFF", wav[:16]
    duration = _wav_duration(wav)
    assert 0.5 <= duration <= 10.0, duration
    with wave.open(__import__("io").BytesIO(wav), "rb") as w:
        assert w.getframerate() == 24000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2


def test_mood_changes_pacing() -> None:
    model = _checkpoint_dir()
    os.environ["PHONON_MODEL_DIR"] = str(model)
    text = "The quick reply arrives before you finish breathing."
    excited = asyncio.run(synthesize_phonon_wav(text, mood="excited"))
    sleepy = asyncio.run(synthesize_phonon_wav(text, mood="sleepy"))
    d_excited = _wav_duration(excited)
    d_sleepy = _wav_duration(sleepy)
    assert d_excited < d_sleepy, (d_excited, d_sleepy)


def test_long_text_concatenates_into_one_wav() -> None:
    model = _checkpoint_dir()
    os.environ["PHONON_MODEL_DIR"] = str(model)
    text = "Sentence one is short. " * 40  # > 220 chars -> multi-chunk path
    wav = asyncio.run(synthesize_phonon_wav(text, mood="normal"))
    assert wav[:4] == b"RIFF", wav[:16]
    duration = _wav_duration(wav)
    assert duration > 8.0, duration


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
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
