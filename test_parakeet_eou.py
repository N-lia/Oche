#!/usr/bin/env python3
"""
Direct Parakeet-EOU-120M (ONNX INT8 160ms) Tester.

Tests NVIDIA Parakeet Real-Time EOU 120M either:
1. Via the on-VM gRPC ASR service (default):
   python test_parakeet_eou.py normal.wav
   python test_parakeet_eou.py normal.wav --target 10.0.0.10:50051
2. Locally via ONNX Runtime (no service required):
   python test_parakeet_eou.py normal.wav --local

Verifies transcription accuracy and <EOU> (End-of-Utterance) token detection.
"""

import argparse
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Terminal colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def test_grpc(audio_file: Path, target: str):
    try:
        from asr_client import transcribe
    except ImportError as exc:
        print(f"{RED}Missing gRPC dependencies: {exc}{RESET}")
        print("Install: pip install grpcio protobuf")
        sys.exit(1)

    print(f"\n{BOLD}{CYAN}=== Testing NVIDIA Parakeet-EOU-120M (gRPC Service) ==={RESET}")
    print(f"Target: {target}")
    print(f"Audio:  {audio_file} ({audio_file.stat().st_size / 1024:.1f} KB)")

    start_time = time.time()
    result = transcribe(audio_file.read_bytes())
    elapsed = time.time() - start_time

    if result.error:
        print(f"{RED}✗ {result.error}{RESET}")
        sys.exit(1)

    print(f"\n{GREEN}✓ Completed in {elapsed:.2f}s ({elapsed*1000:.0f}ms), server {result.duration_s:.2f}s{RESET}")
    print(f"Transcript:    {BOLD}\"{result.text}\"{RESET}")
    print(f"Raw Output:    {result.raw}")
    print(f"EOU Detected:  {GREEN + 'YES (<EOU> fired)' if result.eou_detected else YELLOW + 'NO (Incomplete utterance)'}{RESET}\n")


def test_local(audio_file: Path):
    try:
        import soundfile as sf
        from parakeet_eou_onnx import ParakeetEOU
    except ImportError as e:
        print(f"{RED}Missing dependencies for local test: {e}{RESET}")
        print("Install: pip install onnxruntime numpy soundfile scipy librosa huggingface_hub")
        sys.exit(1)

    print(f"\n{BOLD}{CYAN}=== Testing NVIDIA Parakeet-EOU-120M (Local ONNX INT8) ==={RESET}")
    print(f"Audio: {audio_file} ({audio_file.stat().st_size / 1024:.1f} KB)")

    model = ParakeetEOU()
    audio, rate = sf.read(str(audio_file), dtype="float32")

    result = model.transcribe(audio, sample_rate=rate)
    elapsed = result["duration_s"]

    print(f"\n{GREEN}✓ Completed in {elapsed:.2f}s ({elapsed*1000:.0f}ms){RESET}")
    print(f"Transcript:    {BOLD}\"{result['text']}\"{RESET}")
    print(f"Raw Output:    {result['raw']}")
    print(f"EOU Detected:  {GREEN + 'YES (<EOU> fired)' if result['eou_detected'] else YELLOW + 'NO (Incomplete utterance)'}{RESET}\n")


def main():
    parser = argparse.ArgumentParser(description="Test NVIDIA Parakeet-EOU-120M (ONNX INT8 160ms)")
    parser.add_argument("audio_path", nargs="?", default="normal.wav", help="Path to audio file (WAV/MP3/OGG)")
    parser.add_argument("--target", help="ASR gRPC host:port (defaults to PARAKEET_GRPC_TARGET in .env)")
    parser.add_argument("--local", action="store_true", help="Run locally using pure ONNX Runtime instead of gRPC")
    args = parser.parse_args()

    audio_file = Path(args.audio_path)
    if not audio_file.exists():
        print(f"{RED}Audio file not found: {audio_file}{RESET}")
        sys.exit(1)

    if args.local:
        test_local(audio_file)
        return

    target = (args.target or os.getenv("PARAKEET_GRPC_TARGET") or "localhost:50051").strip()
    test_grpc(audio_file, target)


if __name__ == "__main__":
    main()
