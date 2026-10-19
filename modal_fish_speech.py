"""Modal serverless deployment for Fish Speech 1.5.

Deploy to Modal with:
    modal deploy modal_fish_speech.py

This script:
1. Provisions an NVIDIA L4 GPU (24GB VRAM, ~$0.80/hr, billed per second).
2. Uses a persistent modal.Volume to cache Fish Speech 1.5 weights so subsequent
   starts avoid re-downloading model files.
3. Exposes an HTTPS endpoint (/v1/tts and /speak) that generates friendly audio.
4. Scales down to 0 automatically when idle (zero ongoing cost).
"""

import os
import subprocess
import modal

# Persistent volume for model weights
volume = modal.Volume.from_name("fish-speech-weights", create_if_missing=True)
CHECKPOINT_DIR = "/root/checkpoints/fish-speech-1.5"

# Container environment with PyTorch and Fish Speech dependencies
fish_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libsndfile1")
    .pip_install(
        "torch>=2.4.0",
        "torchaudio>=2.4.0",
        "transformers>=4.45.0",
        "huggingface_hub",
        "fastapi[standard]",
        "ormsgpack",
        "soundfile",
        "httpx",
    )
    .run_commands(
        "git clone https://github.com/fishaudio/fish-speech.git /opt/fish-speech",
        "cd /opt/fish-speech && pip install -e .",
    )
)

app = modal.App("oyi-fish-speech", image=fish_image)


@app.function(
    image=fish_image,
    gpu="L4",                      # NVIDIA L4 (24GB VRAM) - fast and cost effective
    volumes={"/root/checkpoints": volume},
    container_idle_timeout=300,    # Stays warm for 5 minutes during active conversation
    timeout=600,
)
@modal.web_server(port=8080, startup_timeout=300)
def serve():
    """Download weights if missing and launch Fish Speech API server."""
    from huggingface_hub import snapshot_download

    decoder_file = f"{CHECKPOINT_DIR}/firefly-gan-vq-fsq-8x1024-21hz-generator.pth"

    # Step 1: Ensure weights are cached in the persistent volume
    if not os.path.exists(decoder_file):
        print(f"Downloading Fish Speech 1.5 weights to {CHECKPOINT_DIR}...")
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        snapshot_download(
            repo_id="fishaudio/fish-speech-1.5",
            local_dir=CHECKPOINT_DIR,
            local_dir_use_symlinks=False,
        )
        volume.commit()
        print("Weights cached successfully!")

    # Step 2: Start the official Fish Speech API server on port 8080
    cmd = [
        "python",
        "/opt/fish-speech/tools/api_server.py",
        "--llama-checkpoint-path", CHECKPOINT_DIR,
        "--decoder-checkpoint-path", decoder_file,
        "--decoder-config-name", "firefly_gan_vq",
        "--listen", "0.0.0.0:8080",
    ]
    print(f"Launching: {' '.join(cmd)}")
    subprocess.Popen(cmd)
