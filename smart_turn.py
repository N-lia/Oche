"""Semantic end-of-turn detection with Pipecat's Smart Turn v3 model.

Acoustic VAD decides *when the user stopped making noise*; Smart Turn decides
whether that pause was the end of a thought. It is a small Whisper-Tiny
encoder plus a linear head that scores the last 8 seconds of waveform, so an
incomplete "so I was thinking..." can be held open while a finished sentence
is dispatched immediately.

The model and the NumPy feature extractor are vendored here so the ASR
service does not have to pull in the full ``pipecat-ai`` package. Feature
math is ported from Pipecat's BSD-2-Clause ``_whisper_features.py``, which in
turn mirrors Hugging Face's Apache-2.0 ``WhisperFeatureExtractor``.

Model: https://huggingface.co/pipecat-ai/smart-turn-v3
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

LOGGER = logging.getLogger("oyi.asr.smart_turn")

MODEL_REPO = "pipecat-ai/smart-turn-v3"
MODEL_FILE = "smart-turn-v3.2-cpu.onnx"
DEFAULT_MODEL_DIR = "models/smart-turn"
DEFAULT_THRESHOLD = 0.5

_SAMPLE_RATE = 16000
_N_FFT = 400
_HOP_LENGTH = 160
_N_MELS = 80
_MEL_FLOOR = 1e-10
_NORM_VARIANCE_EPS = 1e-7
_MAX_SECONDS = 8


def _hertz_to_mel_slaney(freq: np.ndarray) -> np.ndarray:
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = 27.0 / np.log(6.4)
    freq = np.atleast_1d(np.asarray(freq, dtype=np.float64))
    mels = 3.0 * freq / 200.0
    log_region = freq >= min_log_hertz
    mels[log_region] = min_log_mel + np.log(freq[log_region] / min_log_hertz) * logstep
    return mels


def _mel_to_hertz_slaney(mels: np.ndarray) -> np.ndarray:
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = np.log(6.4) / 27.0
    mels = np.atleast_1d(np.asarray(mels, dtype=np.float64))
    freq = 200.0 * mels / 3.0
    log_region = mels >= min_log_mel
    freq[log_region] = min_log_hertz * np.exp(logstep * (mels[log_region] - min_log_mel))
    return freq


def _build_mel_filterbank(
    num_frequency_bins: int,
    num_mel_filters: int,
    min_frequency: float,
    max_frequency: float,
    sampling_rate: int,
) -> np.ndarray:
    mel_min = float(_hertz_to_mel_slaney(np.array([min_frequency], dtype=np.float64))[0])
    mel_max = float(_hertz_to_mel_slaney(np.array([max_frequency], dtype=np.float64))[0])
    mel_freqs = np.linspace(mel_min, mel_max, num_mel_filters + 2)
    filter_freqs = _mel_to_hertz_slaney(mel_freqs)
    fft_freqs = np.linspace(0, sampling_rate // 2, num_frequency_bins)

    filter_diff = np.diff(filter_freqs)
    slopes = np.expand_dims(filter_freqs, 0) - np.expand_dims(fft_freqs, 1)
    down_slopes = -slopes[:, :-2] / filter_diff[:-1]
    up_slopes = slopes[:, 2:] / filter_diff[1:]
    mel_filters = np.maximum(np.zeros(1), np.minimum(down_slopes, up_slopes))

    enorm = 2.0 / (filter_freqs[2 : num_mel_filters + 2] - filter_freqs[:num_mel_filters])
    mel_filters *= np.expand_dims(enorm, 0)
    return mel_filters


_HANN_WINDOW = np.hanning(_N_FFT + 1)[:-1]
_MEL_FILTERS = _build_mel_filterbank(
    num_frequency_bins=_N_FFT // 2 + 1,
    num_mel_filters=_N_MELS,
    min_frequency=0.0,
    max_frequency=_SAMPLE_RATE / 2.0,
    sampling_rate=_SAMPLE_RATE,
)


def _power_spectrogram(
    waveform: np.ndarray, window: np.ndarray, frame_length: int, hop_length: int
) -> np.ndarray:
    pad = frame_length // 2
    padded = np.pad(waveform.astype(np.float64), (pad, pad), mode="reflect")
    windows = sliding_window_view(padded, frame_length)[::hop_length]
    spec = np.fft.rfft(windows * window.astype(np.float64), axis=-1)
    return (np.abs(spec) ** 2).T


def compute_whisper_log_mel_features(audio: np.ndarray, *, do_normalize: bool = True) -> np.ndarray:
    """Whisper log-mel features (80, 800) for 8 s of 16 kHz audio.

    Shorter inputs are zero-padded, longer inputs are truncated to the leading
    8 s — callers that want the *latest* audio must slice it first.
    """
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim != 1:
        raise ValueError(f"Expected 1-D audio, got shape {x.shape}")

    n_samples = _SAMPLE_RATE * _MAX_SECONDS
    if x.size < n_samples:
        x = np.pad(x, (0, n_samples - x.size), mode="constant")
    elif x.size > n_samples:
        x = x[:n_samples]

    if do_normalize:
        x = (x - x.mean()) / np.sqrt(x.var() + _NORM_VARIANCE_EPS)

    magnitudes = _power_spectrogram(x, _HANN_WINDOW, _N_FFT, _HOP_LENGTH)
    mel_spec = np.maximum(_MEL_FLOOR, _MEL_FILTERS.T @ magnitudes)
    log_spec = np.log10(mel_spec)
    log_spec = log_spec[:, :-1]
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return log_spec.astype(np.float32)


def ensure_model(model_dir: str | Path, *, auto_download: bool = True) -> Path | None:
    """Return the ONNX path, downloading it once into ``model_dir`` if allowed."""
    directory = Path(model_dir)
    path = directory / MODEL_FILE
    if path.exists():
        return path
    if not auto_download:
        return None

    directory.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download

        LOGGER.info("Downloading Smart Turn model %s/%s", MODEL_REPO, MODEL_FILE)
        downloaded = hf_hub_download(
            repo_id=MODEL_REPO, filename=MODEL_FILE, local_dir=str(directory)
        )
        path = Path(downloaded)
    except Exception:  # noqa: BLE001 - smart turn is optional
        LOGGER.exception("Could not download the Smart Turn model")
        return None
    return path if path.exists() else None


class SmartTurnAnalyzer:
    """ONNX semantic end-of-turn scorer for 16 kHz float audio."""

    def __init__(self, model_path: str | Path, *, cpu_count: int = 1) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = max(1, cpu_count)
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(str(model_path), sess_options=options)
        self._input_name = self._session.get_inputs()[0].name
        self.model_path = str(model_path)

    @classmethod
    def load(
        cls,
        model_dir: str | Path,
        *,
        auto_download: bool = True,
        cpu_count: int = 1,
    ) -> "SmartTurnAnalyzer | None":
        path = ensure_model(model_dir, auto_download=auto_download)
        if path is None:
            return None
        return cls(path, cpu_count=cpu_count)

    @staticmethod
    def _resample(audio: np.ndarray, sample_rate: int) -> np.ndarray:
        if sample_rate == _SAMPLE_RATE:
            return audio.astype(np.float32, copy=False)
        from math import gcd

        from scipy.signal import resample_poly

        divisor = gcd(sample_rate, _SAMPLE_RATE)
        return resample_poly(
            audio, _SAMPLE_RATE // divisor, sample_rate // divisor
        ).astype(np.float32)

    def score(self, audio: np.ndarray, sample_rate: int = _SAMPLE_RATE) -> float:
        """Probability in [0, 1] that the speaker has finished their turn."""
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
            raise ValueError("expected finite, non-empty mono audio")

        audio = self._resample(audio, sample_rate)

        # Keep the most recent 8 s. Pad on the *left* so the utterance ends at
        # the model's final frame, matching Pipecat's behaviour.
        max_samples = _SAMPLE_RATE * _MAX_SECONDS
        if len(audio) > max_samples:
            audio = audio[-max_samples:]
        elif len(audio) < max_samples:
            audio = np.pad(audio, (max_samples - len(audio), 0), mode="constant")

        features = compute_whisper_log_mel_features(audio, do_normalize=True)
        outputs = self._session.run(None, {self._input_name: features[None, ...]})
        return float(np.asarray(outputs[0]).reshape(-1)[0])


def score_pcm16(
    analyzer: SmartTurnAnalyzer, pcm: bytes, sample_rate: int = _SAMPLE_RATE
) -> float:
    """Score raw little-endian 16-bit mono PCM."""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    return analyzer.score(samples, sample_rate=sample_rate)


def threshold_from_env(default: float = DEFAULT_THRESHOLD) -> float:
    raw = os.getenv("SMART_TURN_THRESHOLD")
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
