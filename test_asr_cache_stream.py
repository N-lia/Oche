#!/usr/bin/env python3
"""Cache-aware streaming tests for Parakeet-EOU.

The encoder keeps attention/convolution caches and the RNNT decoder keeps its
hidden state between 160 ms chunks, so every chunk is encoded exactly once per
turn. These tests pin three properties:

1. Incremental decoding produces the same transcript as a one-shot decode.
2. A streaming turn runs the encoder the same number of times as one pass —
   i.e. partials are not produced by re-decoding the whole utterance.
3. A non-final step never consumes mel frames that a growing centred STFT can
   still rewrite.

They need the ONNX weights and ``normal.wav``; without them the suite reports
a skip instead of failing on a clean checkout.

Run:
    .venv/bin/python test_asr_cache_stream.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

MODEL_DIR = Path("models/parakeet-eou-160ms")
AUDIO = Path("normal.wav")

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        from parakeet_eou_onnx import ParakeetEOU

        _MODEL = ParakeetEOU(str(MODEL_DIR))
    return _MODEL


def _audio():
    import soundfile as sf

    return sf.read(str(AUDIO), dtype="float32")


def _count_encoder_runs(model) -> list[int]:
    """Wrap the encoder so a test can count per-window invocations."""
    calls = [0]
    original = model.enc.run

    def counting(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    model.enc.run = counting  # type: ignore[method-assign]
    return calls, original


def test_incremental_matches_one_shot() -> None:
    from parakeet_eou_onnx import StreamingSession

    model = _model()
    audio, rate = _audio()
    one_shot = model.transcribe(audio, sample_rate=rate)

    session = StreamingSession(model, sample_rate=rate, decode_interval=0.6)
    events: list[dict] = []
    step = int(rate * 0.1)
    for start in range(0, len(audio), step):
        events.extend(session.push_float(audio[start : start + step]))
    events.extend(session.finalize())

    assert events and events[-1]["type"] == "eou"
    assert events[-1]["text"] == one_shot["text"], (
        f"stream={events[-1]['text']!r} one-shot={one_shot['text']!r}"
    )
    assert events[-1]["model_eou"] == one_shot["eou_detected"]
    assert any(event["type"] == "partial" for event in events)


def test_encoder_runs_once_per_window() -> None:
    from parakeet_eou_onnx import StreamingSession

    model = _model()
    audio, rate = _audio()

    calls, original = _count_encoder_runs(model)
    try:
        audio16 = model._resample(audio, rate)
        features = model._log_mel(audio16, pad_seconds=1.0)
        state = model.new_stream_state()
        model.decode_windows(features, state, final=True)
        one_shot_runs = calls[0]

        calls[0] = 0
        session = StreamingSession(model, sample_rate=rate, decode_interval=0.6)
        step = int(rate * 0.1)
        for start in range(0, len(audio), step):
            session.push_float(audio[start : start + step])
        session.finalize()
        stream_runs = calls[0]
    finally:
        model.enc.run = original  # type: ignore[method-assign]

    # The streaming path recomputes mel features per push, but must never
    # re-run the encoder over windows it already consumed.
    assert stream_runs == one_shot_runs, (
        f"streaming ran the encoder {stream_runs}x vs {one_shot_runs}x for one pass"
    )
    assert one_shot_runs > 10


def test_non_final_step_leaves_unsettled_frames() -> None:
    model = _model()
    window = int(model.config["streaming"]["melFrames"])
    margin = 2
    # 0.9 s is enough for several windows but the tail must stay unconsumed.
    audio = np.zeros(int(16000 * 0.9), dtype=np.float32)
    features = model._log_mel(audio)
    state = model.new_stream_state()
    model.decode_windows(features, state, final=False)

    assert state["mel_pos"] > 0
    assert state["mel_pos"] + window + margin > features.shape[1], (
        "a non-final step consumed frames a centred STFT can still change"
    )


def test_empty_finalize_is_a_no_op() -> None:
    from parakeet_eou_onnx import StreamingSession

    session = StreamingSession(_model())
    assert session.finalize() == []
    assert session.finalize() == []


def _skip_reason() -> str | None:
    if not MODEL_DIR.exists():
        return f"missing ONNX model at {MODEL_DIR}"
    if not AUDIO.exists():
        return f"missing {AUDIO}"
    return None


def main() -> int:
    reason = _skip_reason()
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    if reason:
        print(f"\033[93mSKIP: {reason}\033[0m")
        return 0

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
