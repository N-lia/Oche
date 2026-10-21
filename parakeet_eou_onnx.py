"""
Local / Standalone runner for NVIDIA Parakeet-EOU-120M (ONNX INT8 160ms).

Model: soniqo/Parakeet-EOU-120M-ONNX-INT8-160ms

Streaming is *cache-aware*: the Conformer encoder keeps its attention and
convolution caches between 160 ms chunks, and the RNNT decoder keeps its
hidden state, so each chunk is encoded exactly once. ``StreamingSession`` used
to re-run the whole utterance every 600 ms; it now advances the same caches
incrementally, which turns per-turn encoder work from O(n^2) into O(n).
"""

import json
import os
import time
from math import gcd
from pathlib import Path

import numpy as np

# Mel frames are computed with a centred STFT. The last ``_STABILITY_FRAMES``
# frames of a growing buffer can still change, so a streaming step only
# consumes windows that end at least this many frames behind the newest one.
_STABILITY_FRAMES = 2


class ParakeetEOU:
    def __init__(self, model_dir: str = "models/parakeet-eou-160ms"):
        self.model_dir = Path(model_dir)
        encoder_path = self.model_dir / "parakeet-eou-encoder.onnx"

        if not encoder_path.exists():
            print(f"Downloading soniqo/Parakeet-EOU-120M-ONNX-INT8-160ms to {self.model_dir}...")
            self.model_dir.mkdir(parents=True, exist_ok=True)
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id="soniqo/Parakeet-EOU-120M-ONNX-INT8-160ms",
                local_dir=str(self.model_dir),
                allow_patterns=["*.onnx", "*.json"],
            )
            print("Download completed!")

        import librosa
        import onnxruntime as ort

        self.config = json.loads((self.model_dir / "config.json").read_text())
        self.vocab = json.loads((self.model_dir / "vocab.json").read_text())

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.enc = ort.InferenceSession(
            str(self.model_dir / "parakeet-eou-encoder.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.dec = ort.InferenceSession(
            str(self.model_dir / "parakeet-eou-decoder.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.joint = ort.InferenceSession(
            str(self.model_dir / "parakeet-eou-joint.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

        # Static analysis assets are identical for every chunk: build them once.
        self._window = np.hanning(400)
        self._mel_filters = librosa.filters.mel(
            sr=16000, n_fft=512, n_mels=int(self.config["numMelBins"]), norm="slaney", htk=False
        )
        self._enc_inputs = [entry.name for entry in self.enc.get_inputs()]
        self._cache_shapes = [
            (entry.shape, np.float32 if "float" in entry.type else np.int64)
            for entry in self.enc.get_inputs()[2:]
        ]
        self._window_frames = int(self.config["streaming"]["melFrames"])
        self._stride = int(self.config["streaming"]["outputFrames"]) * int(
            self.config["subsamplingFactor"]
        )

    # ------------------------------------------------------------- features
    def _log_mel(self, audio: np.ndarray, pad_seconds: float = 0.0) -> np.ndarray:
        """128-bin log-mel features, optionally flushed with trailing silence."""
        import librosa

        audio = np.asarray(audio, dtype=np.float32)
        if pad_seconds > 0:
            audio = np.pad(audio, (0, int(16000 * pad_seconds)))

        # Pre-emphasis
        emphasized = audio.copy()
        emphasized[1:] -= 0.97 * audio[:-1]

        spectrum = librosa.stft(
            emphasized,
            n_fft=512,
            hop_length=160,
            win_length=400,
            window=self._window,
            center=True,
            pad_mode="constant",
        )
        return np.log(self._mel_filters @ (np.abs(spectrum) ** 2) + 2**-24).astype(np.float32)

    @staticmethod
    def _resample(audio: np.ndarray, sample_rate: int) -> np.ndarray:
        if sample_rate == 16000:
            return audio
        from scipy.signal import resample_poly

        divisor = gcd(sample_rate, 16000)
        return resample_poly(audio, 16000 // divisor, sample_rate // divisor).astype(np.float32)

    # --------------------------------------------------------- stream state
    def new_stream_state(self) -> dict:
        """Fresh encoder caches plus the initial RNNT decoder state."""
        cfg = self.config
        caches = [
            np.zeros(shape, dtype=dtype) for shape, dtype in self._cache_shapes
        ]
        h = np.zeros(
            (int(cfg["decoderLayers"]), 1, int(cfg["decoderHidden"])), dtype=np.float32
        )
        c = h.copy()
        blank = int(cfg["blankTokenId"])
        hidden, h, c = self.dec.run(
            None, {"token": np.array([[blank]], dtype=np.int64), "h": h, "c": c}
        )
        return {
            "cache": caches,
            "hidden": hidden,
            "h": h,
            "c": c,
            "tokens": [],
            "mel_pos": 0,
            "eou": False,
        }

    def decode_windows(
        self, features: np.ndarray, state: dict, *, final: bool = False
    ) -> dict:
        """Advance ``state`` over every new, settled 160 ms window.

        Returns the state for convenience. On a non-final step the last few
        mel frames are left for a later call because a centred STFT still
        rewrites them as audio arrives; consuming them now would poison the
        encoder cache. ``final=True`` consumes every remaining whole window
        after the caller has padded the audio.
        """
        cfg = self.config
        window = self._window_frames
        stride = self._stride
        margin = 0 if final else _STABILITY_FRAMES
        total = features.shape[1]
        pos = int(state["mel_pos"])
        cache = state["cache"]
        hidden = state["hidden"]
        h = state["h"]
        c = state["c"]
        tokens = state["tokens"]
        blank = int(cfg["blankTokenId"])
        eob = int(cfg["eobTokenId"])
        eou = int(cfg["eouTokenId"])
        output_frames = int(cfg["streaming"]["outputFrames"])

        while pos + window + margin <= total:
            values = [
                features[None, :, pos : pos + window],
                np.array([window], dtype=np.int64),
                *cache,
            ]
            output = self.enc.run(None, dict(zip(self._enc_inputs, values)))
            cache = [val.astype(prev.dtype) for val, prev in zip(output[2:], cache)]

            for frame in range(output_frames):
                for _ in range(8):
                    logits = self.joint.run(
                        None,
                        {
                            "encoder_output": output[0][:, frame : frame + 1],
                            "decoder_output": hidden,
                        },
                    )[0]
                    token = int(logits.argmax())
                    if token == eou:
                        state["eou"] = True
                        break
                    if token == blank:
                        break
                    hidden, h, c = self.dec.run(
                        None, {"token": np.array([[token]], dtype=np.int64), "h": h, "c": c}
                    )
                    if token != eob:
                        tokens.append(token)
            pos += stride

        state.update(
            {"cache": cache, "hidden": hidden, "h": h, "c": c, "tokens": tokens, "mel_pos": pos}
        )
        return state

    def decode_text(self, state: dict) -> str:
        return "".join(self.vocab[str(t)] for t in state["tokens"]).replace("\u2581", " ").strip()

    # ------------------------------------------------------------ one-shot
    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        pad_seconds: float = 1.0,
    ) -> dict:
        """Recognize one audio buffer.

        ``pad_seconds`` appends trailing silence to flush the Conformer's
        streaming context and trigger <EOU>. Use ``0.0`` while streaming
        partials so an unfinished utterance is not cut short.
        """
        start_time = time.time()
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
            raise ValueError("Expected finite, nonempty mono audio")

        audio = self._resample(audio, sample_rate)
        features = self._log_mel(audio, pad_seconds=pad_seconds)
        state = self.new_stream_state()
        self.decode_windows(features, state, final=True)

        clean_text = self.decode_text(state)
        elapsed = time.time() - start_time

        return {
            "text": clean_text,
            "eou_detected": bool(state["eou"]),
            "raw": clean_text + ("<EOU>" if state["eou"] else ""),
            "duration_s": elapsed,
        }


class StreamingSession:
    """Incremental transcription for one spoken turn.

    Audio is accumulated so mel features stay aligned, but only newly settled
    windows are pushed through the encoder: caches and decoder state persist
    across ``push_*`` calls. Partials therefore grow in O(1) encoder work per
    chunk instead of re-decoding the entire utterance.
    """

    def __init__(
        self,
        model: ParakeetEOU,
        sample_rate: int = 16000,
        decode_interval: float = 0.6,
    ) -> None:
        self._model = model
        self._sample_rate = sample_rate or 16000
        self._decode_interval = decode_interval
        self._audio = np.zeros(0, dtype=np.float32)
        self._new_seconds = 0.0
        self._state = model.new_stream_state()
        self._text = ""
        self._eou = False
        self._finished = False

    @property
    def text(self) -> str:
        return self._text

    @property
    def eou_detected(self) -> bool:
        return self._eou

    @property
    def duration_seconds(self) -> float:
        return len(self._audio) / self._sample_rate

    def push_pcm16(self, pcm16: bytes) -> list[dict]:
        """Feed little-endian 16-bit mono PCM and return any new events."""
        if not pcm16 or self._finished:
            return []
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        return self.push_float(samples)

    def push_float(self, samples: np.ndarray) -> list[dict]:
        if samples is None or len(samples) == 0 or self._finished:
            return []
        samples = self._model._resample(np.asarray(samples, dtype=np.float32), self._sample_rate)
        self._audio = np.concatenate([self._audio, samples])
        self._new_seconds += len(samples) / 16000.0
        if self._new_seconds < self._decode_interval:
            return []
        return self._decode(pad_seconds=0.0, final=False)

    def finalize(self) -> list[dict]:
        """Flush the utterance so <EOU> can fire and return the closing events."""
        if self._finished:
            return []
        self._finished = True
        if not len(self._audio):
            return []
        return self._decode(pad_seconds=1.0, final=True)

    def _decode(self, pad_seconds: float, final: bool) -> list[dict]:
        self._new_seconds = 0.0
        features = self._model._log_mel(self._audio, pad_seconds=pad_seconds)
        self._model.decode_windows(features, self._state, final=final)
        text = self._model.decode_text(self._state)
        delta = text[len(self._text):] if text.startswith(self._text) else text
        self._text = text
        self._eou = bool(self._state["eou"]) or final

        events: list[dict] = []
        if delta or final:
            events.append({"type": "partial", "text": text, "delta": delta, "stable": not final})
        # Endpointing belongs to the turn-taking layer (the client VAD plus
        # Smart Turn), not to the ASR: a model EOU in the middle of an
        # utterance must not end the user's turn. ``model_eou`` is still
        # reported on the final event.
        if final:
            events.append(
                {
                    "type": "eou",
                    "text": text,
                    "raw": text + ("<EOU>" if self._state["eou"] else ""),
                    "model_eou": bool(self._state["eou"]),
                }
            )
        return events
