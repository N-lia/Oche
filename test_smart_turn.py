#!/usr/bin/env python3
"""Tests for the Pipecat Smart Turn v3 end-of-turn scorer.

Covers the vendored Whisper feature extractor deterministically and, when the
ONNX weights and ``normal.wav`` are present, checks that a finished sentence
scores higher than the same sentence cut mid-thought.

Run:
    .venv/bin/python test_smart_turn.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from smart_turn import (
    SmartTurnAnalyzer,
    compute_whisper_log_mel_features,
    ensure_model,
    score_pcm16,
)

MODEL_DIR = Path("models/smart-turn")
AUDIO = Path("normal.wav")

_ANALYZER = None


def _analyzer():
    global _ANALYZER
    if _ANALYZER is None:
        _ANALYZER = SmartTurnAnalyzer.load(MODEL_DIR, auto_download=False)
    return _ANALYZER


def test_feature_shape_and_range() -> None:
    rng = np.random.default_rng(7)
    audio = (rng.standard_normal(16000 * 8) * 0.1).astype(np.float32)
    features = compute_whisper_log_mel_features(audio)

    assert features.shape == (80, 800), features.shape
    assert features.dtype == np.float32
    assert np.isfinite(features).all()
    # Normalized log-mel values live roughly in [-1, 1].
    assert features.min() >= -1.5 and features.max() <= 1.5, (
        features.min(),
        features.max(),
    )


def test_short_and_long_audio_map_to_eight_seconds() -> None:
    short = np.zeros(1600, dtype=np.float32)
    long = np.zeros(16000 * 12, dtype=np.float32)
    assert compute_whisper_log_mel_features(short).shape == (80, 800)
    assert compute_whisper_log_mel_features(long).shape == (80, 800)


def test_ensure_model_without_download() -> None:
    assert ensure_model("/nonexistent/smart-turn", auto_download=False) is None


def test_finished_sentence_beats_mid_thought() -> None:
    analyzer = _analyzer()
    assert analyzer is not None, "model missing"

    import soundfile as sf

    audio, rate = sf.read(str(AUDIO), dtype="float32")
    finished = analyzer.score(audio, rate)
    mid_thought = analyzer.score(audio[: int(rate * 4)], rate)

    assert 0.0 <= finished <= 1.0 and 0.0 <= mid_thought <= 1.0
    assert finished > 0.5, f"finished utterance scored {finished:.3f}"
    assert finished > mid_thought, (
        f"finished={finished:.3f} should beat mid_thought={mid_thought:.3f}"
    )


def test_pcm16_matches_float_scoring() -> None:
    analyzer = _analyzer()
    assert analyzer is not None, "model missing"

    rng = np.random.default_rng(11)
    audio = (rng.standard_normal(16000 * 2) * 0.05).astype(np.float32)
    pcm = np.clip(audio * 32768, -32768, 32767).astype("<i2").tobytes()

    float_score = analyzer.score(audio)
    pcm_score = score_pcm16(analyzer, pcm)
    # 16-bit quantization shifts a random-noise score slightly; the point is
    # that the PCM path feeds the same model with the same scaling.
    assert abs(float_score - pcm_score) < 5e-3, (float_score, pcm_score)


def _skip_reason() -> str | None:
    path = MODEL_DIR / "smart-turn-v3.2-cpu.onnx"
    if not path.exists():
        return f"missing Smart Turn model at {path}"
    if not AUDIO.exists():
        return f"missing {AUDIO}"
    return None


def main() -> int:
    reason = _skip_reason()
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    if reason:
        # The feature tests still run without weights.
        tests = [
            t
            for t in tests
            if t.__name__
            in {"test_feature_shape_and_range", "test_short_and_long_audio_map_to_eight_seconds", "test_ensure_model_without_download"}
        ]
        print(f"\033[93mSKIP model tests: {reason}\033[0m")

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
