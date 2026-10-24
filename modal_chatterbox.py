"""
Expressive Agent voice with Chatterbox Turbo on Modal.

Uses Chatterbox-Turbo (350M parameter distilled model) with native
tokenized paralinguistic and emotion actions:
  [laugh], [chuckle], [gasp], [cough], [sigh], [groan], [sniff], [shush],
  [clear throat], [whispering], [angry], [fear], [surprised], [crying],
  [happy], [sarcastic], [dramatic]

Deploy to Modal serverless:
    modal deploy modal_chatterbox.py

Run local/remote demo:
    python modal_chatterbox.py demo
    modal run modal_chatterbox.py::demo
"""

import io
import os
import re
import sys
import modal

REF_DIR = os.environ.get("REF_DIR", "ref")
OUT_DIR = os.environ.get("OUT_DIR", "out")

# event -> mood mapping
EVENT_MOOD = {
    "task_done": "cheerful",
    "slop": "angry",
    "chat": "normal",
    "backchannel": "backchannel",
}

# Dials and configuration parameters per mood
PRESETS = {
    "normal": {
        "exaggeration": 0.5,
        "cfg_weight": 0.5,
        "temperature": 0.8,
        "repetition_penalty": 1.2,
        "min_p": 0.05,
        "top_p": 1.0,
    },
    "cheerful": {
        "exaggeration": 0.7,
        "cfg_weight": 0.3,
        "temperature": 0.8,
        "repetition_penalty": 1.2,
        "min_p": 0.05,
        "top_p": 1.0,
    },
    "angry": {
        "exaggeration": 0.8,
        "cfg_weight": 0.3,
        "temperature": 0.7,
        "repetition_penalty": 1.3,
        "min_p": 0.05,
        "top_p": 1.0,
    },
    # Soft, brief listener signals ("mm-hmm", "right", "go on"). Low energy and
    # lightly expressive so they sit under the user's voice without competing.
    "backchannel": {
        "exaggeration": 0.35,
        "cfg_weight": 0.5,
        "temperature": 0.7,
        "repetition_penalty": 1.2,
        "min_p": 0.05,
        "top_p": 1.0,
    },
}

def call_generate(model_inst, text: str, audio_prompt_path: str | None, **kwargs):
    """Call model_inst.generate with only the keyword arguments supported by its signature."""
    import inspect
    sig = inspect.signature(model_inst.generate)
    has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if has_var_keyword:
        return model_inst.generate(text, audio_prompt_path=audio_prompt_path, **kwargs)
    valid_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return model_inst.generate(text, audio_prompt_path=audio_prompt_path, **valid_kwargs)

# Supported native paralinguistic tokens recognized by Chatterbox-Turbo tokenizer
VALID_TAGS = {
    "[laugh]", "[chuckle]", "[gasp]", "[cough]", "[sigh]",
    "[groan]", "[sniff]", "[shush]", "[clear throat]",
    "[whispering]", "[angry]", "[fear]", "[surprised]",
    "[crying]", "[happy]", "[sarcastic]", "[dramatic]",
    "[narration]", "[advertisement]"
}

# Common LLM-hallucinated variations mapped to official tokens
TAG_SYNONYMS = {
    "[throat-clearing]": "[clear throat]",
    "[throat clearing]": "[clear throat]",
    "[explosive laugh]": "[laugh]",
    "[maniacal cackle]": "[chuckle]",
    "[cackle]": "[chuckle]",
    "[giggle]": "[chuckle]",
    "[cheer]": "[happy]",
    "[dramatic pause]": "... ",
    "[pause]": "... ",
    "[chuckle!]": "[chuckle]",
    "[laugh!]": "[laugh]",
}

chatterbox_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libsndfile1")
    .pip_install(
        "torch>=2.4.0",
        "torchaudio>=2.4.0",
        "transformers>=4.45.0",
        "chatterbox-tts",
        "soundfile",
        "fastapi[standard]",
        "pydantic",
    )
    .env({"HF_HOME": "/data/huggingface"})
)

app = modal.App("oyi-chatterbox-turbo", image=chatterbox_image)
storage_volume = modal.Volume.from_name("chatterbox-weights", create_if_missing=True)


def ref_for(mood: str, ref_dir: str = REF_DIR) -> str | None:
    for d in ("/data/ref", ref_dir):
        p = os.path.join(d, f"{mood}.wav")
        if os.path.exists(p):
            return p
    for d in ("/data/ref", ref_dir):
        p = os.path.join(d, "normal.wav")
        if os.path.exists(p):
            return p
    return None


def sanitize_and_shape_text(text: str, mood: str) -> str:
    """Normalize emotion tags and shape punctuation for prosody."""
    t = text.strip()

    # 1. Map common synonyms to official tokens
    for syn, repl in TAG_SYNONYMS.items():
        t = t.replace(syn, repl)
        t = t.replace(syn.lower(), repl)

    # 2. Strip any unsupported bracketed tags so the TTS doesn't read them literally
    def filter_tag(m: re.Match) -> str:
        tag = m.group(0).lower()
        return tag if tag in VALID_TAGS else ""

    t = re.sub(r"\[[a-zA-Z\s_-]+\]", filter_tag, t)

    # 3. Shape prosody according to mood
    if mood == "cheerful":
        t = re.sub(r"\.$", "!", t)
        if "[happy]" not in t and "[chuckle]" not in t and "[laugh]" not in t:
            t = f"[happy] {t}"
    elif mood == "angry":
        t = re.sub(r",\s*", ". ", t)  # clipped sentences
        t = re.sub(r"\.$", "!", t) if not t.endswith(("!", "?")) else t
        if "[angry]" not in t:
            t = f"[angry] {t}"

    return re.sub(r"\s{2,}", " ", t).strip()


@app.cls(
    gpu="T4",
    volumes={"/data": storage_volume},
    scaledown_window=300,
    timeout=120,
)
class ChatterboxService:
    @modal.enter()
    def load_model(self):
        """Pre-loads Chatterbox-Turbo into GPU VRAM once on container startup."""
        import torch
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        dev = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading ChatterboxTurboTTS on {dev}...")
        self.model = ChatterboxTurboTTS.from_pretrained(device=dev)
        print("ChatterboxTurboTTS is loaded with native paralinguistic tag support!")

    @modal.fastapi_endpoint(method="POST")
    def tts(self, request: dict):
        """HTTP Endpoint for OYI agent.

        Request payload:
        {
            "text": "Done.",
            "event": "task_done" | "slop" | "chat",   (optional)
            "mood": "normal" | "cheerful" | "angry",  (optional override)
            "format": "mp3" | "wav"                   (optional, default "mp3")
        }
        """
        import soundfile as sf
        from fastapi import Response

        text = request.get("text", "").strip()
        event = request.get("event", "chat")
        mood = request.get("mood") or EVENT_MOOD.get(event, "normal")
        fmt = request.get("format", "mp3").lower()

        if not text:
            return Response(content="Missing 'text' parameter", status_code=400)

        # Backchannels are listener noises, not replies: use the model's
        # built-in voice instead of the cloned reference used for full replies.
        ref = None if mood == "backchannel" else ref_for(mood)
        shaped = sanitize_and_shape_text(text, mood)

        # Dial settings: load mood preset and allow request-level overrides
        params = PRESETS.get(mood, PRESETS["normal"]).copy()
        for k in ("exaggeration", "cfg_weight", "temperature", "top_p", "min_p", "repetition_penalty"):
            if k in request and request[k] is not None:
                try:
                    params[k] = float(request[k])
                except (ValueError, TypeError):
                    pass

        print(f"[Chatterbox-Turbo] Generating mood='{mood}' ref='{ref}' dials={params}: \"{shaped}\"")

        # Generate audio using 1-step mel decoding with paralinguistic action tags and dials
        wav = call_generate(self.model, shaped, audio_prompt_path=ref, **params)
        wav_np = wav.squeeze().cpu().numpy()

        buffer = io.BytesIO()
        if fmt == "wav":
            sf.write(buffer, wav_np, self.model.sr, format="WAV")
            media_type = "audio/wav"
        else:
            sf.write(buffer, wav_np, self.model.sr, format="MP3")
            media_type = "audio/mpeg"

        buffer.seek(0)
        return Response(content=buffer.read(), media_type=media_type)


# CLI helper for standalone testing or demo
_model = None

def model():
    global _model
    if _model is None:
        import torch
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        dev = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        _model = ChatterboxTurboTTS.from_pretrained(device=dev)
    return _model


def speak(
    text: str,
    event: str = "chat",
    path: str | None = None,
    mood: str | None = None,
    **kwargs,
) -> str:
    import torchaudio as ta
    mood = mood or EVENT_MOOD.get(event, "normal")
    params = PRESETS.get(mood, PRESETS["normal"]).copy()
    params.update({k: v for k, v in kwargs.items() if v is not None})
    # Same rule as the HTTP endpoint: backchannels stay on the default voice.
    ref = None if mood == "backchannel" else ref_for(mood)
    shaped = sanitize_and_shape_text(text, mood)
    wav = call_generate(model(), shaped, audio_prompt_path=ref, **params)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = path or os.path.join(OUT_DIR, f"{event}_{mood}.wav")
    ta.save(path, wav, model().sr)
    return path


@app.function(gpu="T4", volumes={"/data": storage_volume})
def demo():
    lines = {
        "task_done": "Build passed, deployed, and the tests are all green! [laugh]",
        "slop": "You pushed that without running the tests. Again. [sigh]",
        "chat": "Sure, I can look into that [chuckle]. What file is it in?",
    }
    for ev, line in lines.items():
        p = speak(line, ev)
        print(f"Generated {ev} -> {p}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        lines = {
            "task_done": "Build passed, deployed, and the tests are all green! [laugh]",
            "slop": "You pushed that without running the tests. Again. [sigh]",
            "chat": "Sure, I can look into that [chuckle]. What file is it in?",
        }
        for ev, line in lines.items():
            print(speak(line, ev))
    else:
        print("usage: python modal_chatterbox.py demo")
