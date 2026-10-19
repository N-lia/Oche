#!/usr/bin/env python3
"""
Direct Chatterbox-Turbo TTS Tester (No LLM in the Loop).

Directly sends text prompts to your Chatterbox Modal endpoint, synthesizes audio,
displays metrics (latency & size), and saves the resulting MP3/WAV.

Usage:
  1. Interactive mode (prompts loop):
     python test_chatterbox_config.py

  2. One-shot command line:
     python test_chatterbox_config.py "Hello Sir Mario! [chuckle] System is ready."
     python test_chatterbox_config.py "[happy] All tests passed!" --mood cheerful --play
     python test_chatterbox_config.py "[angry] Stop doing that! [sigh]" --mood angry -o test.mp3
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Try to import dependencies; provide helpful instructions if missing
try:
    import httpx
    from dotenv import load_dotenv
except ImportError:
    print("\n[!] Missing dependencies. Please run with your virtual environment:")
    print("    .venv/bin/python test_chatterbox_config.py")
    print("    or: pip install httpx python-dotenv\n")
    sys.exit(1)

# Load environment
load_dotenv()

# Terminal colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

VALID_TAGS = [
    "[laugh]", "[chuckle]", "[gasp]", "[cough]", "[sigh]",
    "[groan]", "[sniff]", "[shush]", "[clear throat]",
    "[whispering]", "[angry]", "[fear]", "[surprised]",
    "[crying]", "[happy]", "[sarcastic]", "[dramatic]",
    "[narration]", "[advertisement]"
]


def print_tags():
    print(f"\n{BOLD}Chatterbox-Turbo Paralinguistic & Emotion Tokens:{RESET}")
    print(f"{CYAN}Physical Actions (use inline):{RESET}")
    print("  [laugh] [chuckle] [gasp] [cough] [sigh] [groan] [sniff] [shush] [clear throat]")
    print(f"{CYAN}Emotional States (use at start):{RESET}")
    print("  [whispering] [happy] [angry] [fear] [surprised] [crying] [sarcastic] [dramatic]")
    print(f"{CYAN}Styles:{RESET}")
    print("  [narration] [advertisement]\n")


def get_default_url() -> str:
    return (
        os.getenv("CHATTERBOX_MODAL_URL")
        or os.getenv("CHATTERBOX_MOODAL_URL")
        or ""
    ).strip().rstrip("/")


def play_audio(filepath: Path) -> None:
    """Attempt to play the audio file using available system utilities."""
    players = [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(filepath)],
        ["mpv", "--no-video", str(filepath)],
        ["aplay", str(filepath)],
        ["paplay", str(filepath)],
    ]
    for cmd in players:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass


def synthesize_direct(
    url: str,
    text: str,
    mood: str = "normal",
    event: str = "chat",
    fmt: str = "mp3",
    dials: dict | None = None,
    timeout: float = 90.0,
) -> tuple[bytes, float]:
    """Send text directly to Modal endpoint and return (audio_bytes, elapsed_seconds)."""
    payload = {
        "text": text,
        "mood": mood,
        "event": event,
        "format": fmt,
    }
    if dials:
        for k, v in dials.items():
            if v is not None:
                payload[k] = v

    start_time = time.time()
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload)
        elapsed = time.time() - start_time

        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

        content = response.content
        if not content:
            raise RuntimeError("Endpoint returned empty audio payload")

        return content, elapsed


def interactive_mode(url: str, auto_play: bool = False, output_dir: str = "out"):
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    current_mood = "normal"
    current_dials = {}

    print(f"\n{BOLD}{CYAN}=== Chatterbox-Turbo Interactive Tester (Direct Modal, No LLM) ==={RESET}")
    print(f"Target URL: {GREEN}{url}{RESET}")
    print(f"Auto-play:  {'Enabled' if auto_play else 'Disabled'}")
    print(f"\nCommands:")
    print(f"  {YELLOW}/mood <normal|cheerful|angry>{RESET} -> Change emotional tone")
    print(f"  {YELLOW}/dials{RESET}                         -> View or tweak dials (exaggeration, cfg_weight, etc.)")
    print(f"  {YELLOW}/play{RESET}                          -> Toggle auto-play on/off")
    print(f"  {YELLOW}/tags{RESET}                          -> Display available action/emotion tags")
    print(f"  {YELLOW}/quit{RESET} or {YELLOW}/exit{RESET}                  -> Exit tester\n")

    while True:
        try:
            prompt = input(f"{BOLD}Prompt [{current_mood}] > {RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break

        if not prompt:
            continue

        # Handle commands
        if prompt in ("/exit", "/quit", "exit", "quit"):
            print("Goodbye!")
            break
        elif prompt == "/tags":
            print_tags()
            continue
        elif prompt == "/play":
            auto_play = not auto_play
            print(f"Auto-play is now: {GREEN if auto_play else YELLOW}{'Enabled' if auto_play else 'Disabled'}{RESET}")
            continue
        elif prompt.startswith("/mood"):
            parts = prompt.split()
            if len(parts) > 1 and parts[1].lower() in ("normal", "cheerful", "angry"):
                current_mood = parts[1].lower()
                print(f"Mood updated to: {GREEN}{current_mood}{RESET}")
            else:
                print(f"{YELLOW}Usage: /mood <normal | cheerful | angry>{RESET}")
            continue
        elif prompt == "/dials":
            print(f"\nCurrent active dial overrides: {current_dials or 'None (using mood preset defaults)'}")
            print("Set a dial with: /dial <name> <value> (e.g. /dial exaggeration 0.75)")
            print("Reset all dials with: /dial reset\n")
            continue
        elif prompt.startswith("/dial"):
            parts = prompt.split()
            if len(parts) == 2 and parts[1] == "reset":
                current_dials.clear()
                print(f"{GREEN}Dials reset to defaults.{RESET}")
            elif len(parts) == 3:
                name, val = parts[1], parts[2]
                try:
                    current_dials[name] = float(val)
                    print(f"{GREEN}Set {name} = {val}{RESET}")
                except ValueError:
                    print(f"{RED}Invalid numeric value for {name}{RESET}")
            else:
                print(f"{YELLOW}Usage: /dial <name> <val>  or  /dial reset{RESET}")
            continue

        # Process text synthesis
        print(f"{DIM}Synthesizing audio on Modal...{RESET}", end="", flush=True)
        try:
            audio_bytes, elapsed = synthesize_direct(
                url=url,
                text=prompt,
                mood=current_mood,
                dials=current_dials,
            )
            # Erase loading text
            print(f"\r{GREEN}✓ Synthesized in {elapsed:.2f}s ({len(audio_bytes)/1024:.1f} KB){RESET}")

            # Save file
            timestamp = datetime.now().strftime("%H%M%S")
            save_file = out_path / f"speech_{current_mood}_{timestamp}.mp3"
            save_file.write_bytes(audio_bytes)

            latest_file = out_path / "latest.mp3"
            latest_file.write_bytes(audio_bytes)
            print(f"  Saved: {save_file} (and {latest_file})")

            if auto_play:
                play_audio(latest_file)

        except Exception as e:
            print(f"\r{RED}✗ Failed: {e}{RESET}")


def main():
    parser = argparse.ArgumentParser(
        description="Direct Chatterbox-Turbo TTS Tester (No LLM in the loop)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", nargs="?", help="Text to synthesize directly")
    parser.add_argument("-m", "--mood", choices=["normal", "cheerful", "angry"], default="normal", help="Voice mood preset")
    parser.add_argument("-e", "--event", choices=["chat", "task_done", "slop"], default="chat", help="Event context")
    parser.add_argument("-o", "--out", help="Output file path (default: out/speech.mp3)")
    parser.add_argument("-f", "--format", choices=["mp3", "wav"], default="mp3", help="Audio format")
    parser.add_argument("-u", "--url", help="Modal endpoint URL (defaults to CHATTERBOX_MODAL_URL in .env)")
    parser.add_argument("-p", "--play", action="store_true", help="Automatically play synthesized audio")
    parser.add_argument("--tags", action="store_true", help="Print supported paralinguistic and emotion tags")

    # Dials
    parser.add_argument("--exaggeration", type=float, help="Dial: expressiveness & speed (0.56))")
    parser.add_argument("--cfg-weight", type=float, help="Dial: reference adherence & pacing (0.01)")
    parser.add_argument("--temperature", type=float, help="Dial: sampling variability (1.5)")
    parser.add_argument("--top-p", type=float, help="Dial: nucleus sampling focus (0.8)")
    parser.add_argument("--min-p", type=float, help="Dial: tail filtering cutoff (0.08)")
    parser.add_argument("--repetition-penalty", type=float, help="Dial: loop/stutter prevention (2.06)")

    args = parser.parse_args()

    if args.tags:
        print_tags()
        return

    url = args.url or get_default_url()
    if not url:
        print(f"{RED}Error: CHATTERBOX_MODAL_URL is not set in your .env and no --url was provided.{RESET}")
        sys.exit(1)

    # Collect custom dial overrides if passed
    dials = {}
    for dial in ("exaggeration", "cfg_weight", "temperature", "top_p", "min_p", "repetition_penalty"):
        val = getattr(args, dial.replace("-", "_"), None)
        if val is not None:
            dials[dial] = val

    # If no prompt provided on CLI, enter interactive loop
    if not args.prompt:
        interactive_mode(url, auto_play=args.play)
        return

    # One-shot mode
    print(f"\n{BOLD}Chatterbox-Turbo Direct TTS{RESET}")
    print(f"Target: {CYAN}{url}{RESET}")
    print(f"Mood:   {args.mood} (event: {args.event})")
    print(f"Prompt: \"{args.prompt}\"")
    if dials:
        print(f"Dials:  {dials}")

    print(f"\n{DIM}Calling Modal endpoint...{RESET}", end="", flush=True)
    try:
        audio_bytes, elapsed = synthesize_direct(
            url=url,
            text=args.prompt,
            mood=args.mood,
            event=args.event,
            fmt=args.format,
            dials=dials,
        )
        print(f"\r{GREEN}✓ Synthesized in {elapsed:.2f}s ({len(audio_bytes)/1024:.1f} KB){RESET}")

        out_path = Path(args.out or f"out/speech_{args.mood}.{args.format}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(audio_bytes)
        print(f"Audio saved to: {BOLD}{out_path}{RESET}")

        if args.play:
            print("Playing audio...")
            play_audio(out_path)

    except Exception as e:
        print(f"\r{RED}✗ Failed: {e}{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
