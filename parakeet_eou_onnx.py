"""
Local / Standalone runner for NVIDIA Parakeet-EOU-120M (ONNX INT8 160ms).

Model: soniqo/Parakeet-EOU-120M-ONNX-INT8-160ms
"""

import json
import os
import time
from math import gcd
from pathlib import Path
import numpy as np


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
        import librosa
        from scipy.signal import resample_poly

        start_time = time.time()
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
            raise ValueError("Expected finite, nonempty mono audio")

        if sample_rate != 16000:
            divisor = gcd(sample_rate, 16000)
            audio = resample_poly(audio, 16000 // divisor, sample_rate // divisor)

        # Pad trailing silence to flush Conformer context & trigger EOU
        if pad_seconds > 0:
            audio = np.pad(audio, (0, int(16000 * pad_seconds)))

        # Pre-emphasis
        emphasized = audio.copy()
        emphasized[1:] -= 0.97 * audio[:-1]

        # Mel filterbank extraction (128-bin, n_fft=512, hop=160, win=400)
        spectrum = librosa.stft(
            emphasized,
            n_fft=512,
            hop_length=160,
            win_length=400,
            window=np.hanning(400),
            center=True,
            pad_mode="constant",
        )
        filters = librosa.filters.mel(sr=16000, n_fft=512, n_mels=128, norm="slaney", htk=False)
        features = np.log(filters @ (np.abs(spectrum) ** 2) + 2**-24).astype(np.float32)

        cfg = self.config
        shapes = [entry.shape for entry in self.enc.get_inputs()[2:]]
        cache = [
            np.zeros(shape, dtype=np.float32 if "float" in entry.type else np.int64)
            for shape, entry in zip(shapes, self.enc.get_inputs()[2:])
        ]

        h = np.zeros((cfg["decoderLayers"], 1, cfg["decoderHidden"]), dtype=np.float32)
        c = h.copy()
        blank = cfg["blankTokenId"]

        # Run blank through decoder
        hidden, h, c = self.dec.run(
            None, {"token": np.array([[blank]], dtype=np.int64), "h": h, "c": c}
        )

        tokens = []
        eou_detected = False
        window = cfg["streaming"]["melFrames"]
        stride = cfg["streaming"]["outputFrames"] * cfg["subsamplingFactor"]
        input_names = [entry.name for entry in self.enc.get_inputs()]

        for offset in range(0, features.shape[1] - window + 1, stride):
            values = [
                features[None, :, offset : offset + window],
                np.array([window], dtype=np.int64),
                *cache,
            ]
            output = self.enc.run(None, dict(zip(input_names, values)))
            cache = [val.astype(prev.dtype) for val, prev in zip(output[2:], cache)]

            for frame in range(cfg["streaming"]["outputFrames"]):
                for _ in range(8):
                    logits = self.joint.run(
                        None,
                        {
                            "encoder_output": output[0][:, frame : frame + 1],
                            "decoder_output": hidden,
                        },
                    )[0]
                    token = int(logits.argmax())
                    if token == cfg["eouTokenId"]:
                        eou_detected = True
                        break
                    if token == blank:
                        break
                    hidden, h, c = self.dec.run(
                        None, {"token": np.array([[token]], dtype=np.int64), "h": h, "c": c}
                    )
                    if token != cfg["eobTokenId"]:
                        tokens.append(token)

        clean_text = "".join(self.vocab[str(t)] for t in tokens).replace("\u2581", " ").strip()
        elapsed = time.time() - start_time

        return {
            "text": clean_text,
            "eou_detected": eou_detected,
            "raw": clean_text + ("<EOU>" if eou_detected else ""),
            "duration_s": elapsed,
        }


class StreamingSession:
    """Incremental transcription for one spoken turn.

    Audio is accumulated and re-decoded on an interval so the caller receives
    growing partial transcripts and an end-of-utterance signal. Re-decoding the
    validated ``ParakeetEOU.transcribe`` path keeps partials correct without
    depending on fragile cache alignment; a future optimization can swap in
    true cache-based decoding behind this same interface.
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
        self._audio = np.concatenate([self._audio, np.asarray(samples, dtype=np.float32)])
        self._new_seconds += len(samples) / self._sample_rate
        if self._new_seconds < self._decode_interval:
            return []
        return self._decode(pad_seconds=0.0)

    def finalize(self) -> list[dict]:
        """Flush the utterance so <EOU> can fire and return the closing events."""
        if self._finished:
            return []
        self._finished = True
        if not len(self._audio):
            return []
        return self._decode(pad_seconds=1.0, final=True)

    def _decode(self, pad_seconds: float, final: bool = False) -> list[dict]:
        self._new_seconds = 0.0
        result = self._model.transcribe(self._audio, sample_rate=self._sample_rate, pad_seconds=pad_seconds)
        text = result.get("text", "") or ""
        delta = text[len(self._text):] if text.startswith(self._text) else text
        self._text = text
        self._eou = bool(result.get("eou_detected", False)) or final

        events: list[dict] = []
        if delta or final:
            events.append({"type": "partial", "text": text, "delta": delta, "stable": not final})
        # Endpointing belongs to the turn-taking layer (the client VAD), not to
        # the ASR: a model EOU in the middle of an utterance must not end the
        # user's turn. ``model_eou`` is still reported on the final event.
        if final:
            events.append(
                {
                    "type": "eou",
                    "text": text,
                    "raw": result.get("raw", text),
                    "model_eou": bool(result.get("eou_detected", False)),
                }
            )
        return events
