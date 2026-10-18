"""
NVIDIA Parakeet-EOU-120M (ONNX INT8 160ms) Service on Modal.

Model: soniqo/Parakeet-EOU-120M-ONNX-INT8-160ms
Features:
- FastConformer RNN-T with INT8 quantized encoder
- Ultra-low latency 160ms streaming chunks
- Native End-of-Utterance (<EOU>) token detection
- Pure ONNX Runtime CPU inference (zero Torch / Torchaudio / TorchCodec dependencies)

Deploy to Modal:
    modal deploy modal_parakeet.py
"""

import base64
import io
import json
import os
from math import gcd
from pathlib import Path
import modal

parakeet_image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "ffmpeg", "libsndfile1")
    .pip_install(
        "onnxruntime",
        "numpy",
        "soundfile",
        "scipy",
        "librosa",
        "huggingface_hub",
        "fastapi[standard]",
        "pydantic",
        "python-multipart",
    )
    .env({"HF_HOME": "/data/huggingface"})
)

app = modal.App("oyi-parakeet-eou", image=parakeet_image)
storage_volume = modal.Volume.from_name("parakeet-weights", create_if_missing=True)


@app.cls(
    cpu=2,
    memory=2048,
    volumes={"/data": storage_volume},
    scaledown_window=300,
    timeout=120,
)
class ParakeetService:
    @modal.enter()
    def load_model(self):
        """Downloads (if not cached) and loads the INT8 160ms ONNX models."""
        from huggingface_hub import snapshot_download
        import onnxruntime as ort

        model_dir = "/data/parakeet-eou-160ms"
        encoder_path = os.path.join(model_dir, "parakeet-eou-encoder.onnx")

        if not os.path.exists(encoder_path):
            print("Downloading soniqo/Parakeet-EOU-120M-ONNX-INT8-160ms from Hugging Face...")
            snapshot_download(
                repo_id="soniqo/Parakeet-EOU-120M-ONNX-INT8-160ms",
                local_dir=model_dir,
                allow_patterns=["*.onnx", "*.json"],
            )
            print("Downloaded model weights successfully!")

        root = Path(model_dir)
        self.config = json.loads((root / "config.json").read_text())
        self.vocab = json.loads((root / "vocab.json").read_text())

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        print("Initializing ONNX Runtime inference sessions...")
        self.enc = ort.InferenceSession(
            str(root / "parakeet-eou-encoder.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.dec = ort.InferenceSession(
            str(root / "parakeet-eou-decoder.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.joint = ort.InferenceSession(
            str(root / "parakeet-eou-joint.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        print("Parakeet-EOU-120M ONNX INT8 (160ms) ready!")

    @modal.fastapi_endpoint(method="POST")
    def transcribe(self, request: dict):
        """
        HTTP Endpoint for audio transcription + EOU detection.

        Request:
        {
            "audio_base64": "<base64 encoded audio>",
            "format": "wav" (optional)
        }

        Response:
        {
            "text": "transcribed text",
            "eou_detected": true/false,
            "raw": "transcribed text<EOU>"
        }
        """
        import librosa
        import numpy as np
        from scipy.signal import resample_poly
        import soundfile as sf

        audio_b64 = request.get("audio_base64", "")
        if not audio_b64:
            return {"error": "Missing audio_base64 in request payload", "text": "", "eou_detected": False}

        import subprocess
        import tempfile

        tmp_in_path = ""
        tmp_wav_path = ""
        try:
            audio_bytes = base64.b64decode(audio_b64)
            with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp_in:
                tmp_in.write(audio_bytes)
                tmp_in_path = tmp_in.name

            tmp_wav_path = tmp_in_path + ".wav"
            cmd = [
                "ffmpeg", "-y", "-i", tmp_in_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", tmp_wav_path,
            ]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if res.returncode == 0 and os.path.exists(tmp_wav_path):
                audio, sample_rate = sf.read(tmp_wav_path, dtype="float32")
            else:
                audio, sample_rate = sf.read(tmp_in_path, dtype="float32")
        except Exception as e:
            return {"error": f"Failed to decode audio: {e}", "text": "", "eou_detected": False}
        finally:
            for p in (tmp_in_path, tmp_wav_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

        try:
            audio = np.asarray(audio, dtype=np.float32)
            if audio.ndim == 2:
                audio = audio.mean(axis=1)
            if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
                return {"error": "Expected finite, nonempty audio", "text": "", "eou_detected": False}

            # Resample to 16kHz if needed
            if sample_rate != 16000:
                divisor = gcd(sample_rate, 16000)
                audio = resample_poly(audio, 16000 // divisor, sample_rate // divisor)

            # Pad 1 sec trailing silence to flush Conformer streaming context & trigger EOU
            audio = np.pad(audio, (0, 16000))

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

            # Initialize decoder with blank token
            hidden, h, c = self.dec.run(
                None, {"token": np.array([[blank]], dtype=np.int64), "h": h, "c": c}
            )

            tokens = []
            eou_detected = False
            window = cfg["streaming"]["melFrames"]
            stride = cfg["streaming"]["outputFrames"] * cfg["subsamplingFactor"]
            input_names = [entry.name for entry in self.enc.get_inputs()]

            # Streaming chunk step
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
            raw_str = clean_text + ("<EOU>" if eou_detected else "")

            return {
                "text": clean_text,
                "eou_detected": eou_detected,
                "raw": raw_str,
            }
        except Exception as exc:
            return {"error": str(exc), "text": "", "eou_detected": False}
