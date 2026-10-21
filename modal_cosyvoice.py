"""
CosyVoice 2 TTS on Modal — an A/B alternative to Chatterbox Turbo.

This is a separate Modal app (``oyi-cosyvoice``) so both voices can run at the
same time. It speaks the same HTTP contract as ``modal_chatterbox.py``
(``text``/``mood``/``event``/``format``), which lets ``voice.py`` switch with
``TTS_PROVIDER=cosyvoice`` without touching the bot or the WebApp.

Deploy:
    modal deploy modal_cosyvoice.py

The first container downloads ``FunAudioLLM/CosyVoice2-0.5B`` into the
``cosyvoice-weights`` volume (a few GB) and commits it, so later cold starts
skip the download. The reference timbre is the same ``normal.wav`` used by
Chatterbox, mounted read-only from the ``chatterbox-weights`` volume.

Generation modes:
    cross_lingual (default)  timbre only; no prompt transcript needed
    zero_shot                timbre + prompt_text (more faithful if accurate)

Try it directly:
    modal run modal_cosyvoice.py::demo
"""

from __future__ import annotations

import io
import os
import re

import modal

MODEL_REPO = "FunAudioLLM/CosyVoice2-0.5B"
MODEL_DIR = "/models/CosyVoice2-0.5B"
REF_PATH = "/refs/ref/normal.wav"
PROMPT_TEXT = (
    "this is me recording my voice for chatterbox so my agent can learn how to "
    "speak like me and use my voice as an example"
)

# Mood -> speaking-rate nudge. CosyVoice has no paralinguistic tags, so the
# best we can do is shape pace and let the reference timbre carry the rest.
MOOD_SPEED = {
    "normal": 1.0,
    "cheerful": 1.06,
    "angry": 1.03,
    "backchannel": 1.0,
}

cosyvoice_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04", add_python="3.10"
    )
    .apt_install("git", "ffmpeg", "libsndfile1", "sox", "libsox-dev", "build-essential")
    .run_commands(
        "git clone --depth 1 --recursive https://github.com/FunAudioLLM/CosyVoice.git /opt/CosyVoice"
    )
    # openai-whisper only ships an sdist whose setup.py imports pkg_resources.
    # pip's isolated build env now resolves setuptools>=82, which removed that
    # module, so build it against a pinned setuptools instead.
    .pip_install("setuptools<81", "wheel")
    .pip_install(
        "numpy==1.26.4",
        "torch==2.3.1",
        "torchaudio==2.3.1",
        "transformers==4.51.3",
        "hyperpyyaml==1.2.3",
        "omegaconf==2.3.0",
        "hydra-core==1.3.2",
        "conformer==0.3.2",
        "diffusers==0.29.0",
        "x-transformers==2.11.24",
        "librosa==0.10.2",
        "soundfile==0.12.1",
        "onnxruntime-gpu==1.18.0",
        "modelscope==1.20.0",
        "tiktoken",
        "inflect==7.3.1",
        "wetext==0.0.4",
        "matplotlib==3.7.5",
        "networkx==3.1",
        "fastapi[standard]",
        "huggingface_hub",
    )
    # CosyVoice's flow-matching modules import `lightning` at import time;
    # without it the container dies during Runner startup.
    .pip_install("lightning==2.2.4", "pytorch-lightning==2.2.4")
    .run_commands("python -m pip install --no-build-isolation openai-whisper==20231117")
    .env(
        {
            "PYTHONPATH": "/opt/CosyVoice:/opt/CosyVoice/third_party/Matcha-TTS",
            "HF_HOME": "/models/hf",
        }
    )
)

app = modal.App("oyi-cosyvoice", image=cosyvoice_image)
weights = modal.Volume.from_name("cosyvoice-weights", create_if_missing=True)
refs = modal.Volume.from_name("chatterbox-weights")


def _ensure_model() -> None:
    marker = os.path.join(MODEL_DIR, "llm.pt")
    if os.path.exists(marker):
        return
    print(f"Downloading {MODEL_REPO} to {MODEL_DIR} (first start only)…")
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=MODEL_REPO, local_dir=MODEL_DIR)
    weights.commit()
    print("CosyVoice 2 weights cached in the volume.")


def strip_tags(text: str) -> str:
    """Remove Chatterbox-style paralinguistic tags CosyVoice would read aloud."""
    return re.sub(r"\[[a-zA-Z\s_-]+\]", "", text or "").strip()


def encode_audio(wav, sample_rate: int, fmt: str) -> tuple[bytes, str]:
    """Encode a float waveform to MP3 (or WAV) bytes."""
    import numpy as np
    import soundfile as sf

    buffer = io.BytesIO()
    if fmt == "wav":
        sf.write(buffer, np.asarray(wav, dtype=np.float32), sample_rate, format="WAV")
        media_type = "audio/wav"
    else:
        sf.write(buffer, np.asarray(wav, dtype=np.float32), sample_rate, format="MP3")
        media_type = "audio/mpeg"
    buffer.seek(0)
    return buffer.read(), media_type


@app.cls(
    gpu="T4",
    volumes={"/models": weights, "/refs": refs},
    scaledown_window=300,
    timeout=1800,
    # Model load takes >150s, which trips the Modal HTTP proxy's request
    # timeout on a cold start ("bad redirect" 303). Keep one container warm so
    # live requests always hit a loaded model.
    min_containers=1,
)
class CosyVoiceService:
    @modal.enter()
    def load_model(self):
        import torch
        from cosyvoice.cli.cosyvoice import CosyVoice2

        _ensure_model()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading CosyVoice2-0.5B on {device}…")
        self.model = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False, fp16=False)
        self.sample_rate = int(self.model.sample_rate)
        if not os.path.exists(REF_PATH):
            raise RuntimeError(
                f"Reference voice missing at {REF_PATH}. "
                "Run: modal volume put chatterbox-weights <normal.wav> /ref/normal.wav"
            )
        # Compile kernels once so the first user turn is not the warm-up.
        try:
            list(self.model.inference_cross_lingual("Ready.", REF_PATH, stream=False))
            print("CosyVoice warm-up complete.")
        except Exception:  # noqa: BLE001 - warm-up is best effort
            print("CosyVoice warm-up skipped (will warm on first request).")

    @modal.fastapi_endpoint(method="POST")
    def tts(self, request: dict):
        """HTTP endpoint compatible with the Chatterbox contract."""
        from fastapi import Response

        text = strip_tags(request.get("text", ""))
        event = request.get("event", "chat")
        mood = request.get("mood") or "normal"
        fmt = (request.get("format") or "mp3").lower()
        mode = (request.get("mode") or "cross_lingual").lower()
        speed = float(request.get("speed") or MOOD_SPEED.get(mood, 1.0))

        if not text:
            return Response(content="Missing 'text' parameter", status_code=400)

        prompt_text = request.get("prompt_text") or PROMPT_TEXT
        print(f"[CosyVoice2] mood={mood} mode={mode} speed={speed} event={event}: {text!r}")

        try:
            if mode == "zero_shot":
                pieces = list(
                    self.model.inference_zero_shot(
                        text, prompt_text, REF_PATH, stream=False, speed=speed
                    )
                )
            else:
                pieces = list(
                    self.model.inference_cross_lingual(
                        text, REF_PATH, stream=False, speed=speed
                    )
                )
            import torch

            if not pieces:
                raise RuntimeError("CosyVoice returned no audio")
            speech = torch.cat([piece["tts_speech"] for piece in pieces], dim=1)
            wav = speech.squeeze(0).detach().cpu().float().numpy()
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            print(f"[CosyVoice2] generation failed: {exc}")
            return Response(content=f"CosyVoice generation failed: {exc}", status_code=500)

        audio, media_type = encode_audio(wav, self.sample_rate, fmt)
        return Response(content=audio, media_type=media_type)


@app.function(gpu="T4", volumes={"/models": weights, "/refs": refs}, timeout=1200)
def demo():
    """Generate the same three lines Chatterbox demos, for a side-by-side listen."""
    import torch
    from cosyvoice.cli.cosyvoice import CosyVoice2

    _ensure_model()
    model = CosyVoice2(MODEL_DIR)
    lines = {
        "task_done": "Build passed, deployed, and the tests are all green!",
        "slop": "You pushed that without running the tests. Again.",
        "chat": "Sure, I can look into that. What file is it in?",
    }
    for name, line in lines.items():
        pieces = list(model.inference_cross_lingual(line, REF_PATH, stream=False))
        speech = torch.cat([p["tts_speech"] for p in pieces], dim=1)
        audio, _ = encode_audio(speech.squeeze(0).cpu().numpy(), int(model.sample_rate), "mp3")
        path = f"out/cosyvoice_{name}.mp3"
        os.makedirs("out", exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(audio)
        print(f"Wrote {path} ({len(audio)} bytes)")


if __name__ == "__main__":
    print("Deploy:  modal deploy modal_cosyvoice.py")
    print("Demo:    modal run modal_cosyvoice.py::demo")
