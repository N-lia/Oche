"""Test script for deployed Chatterbox Turbo on Modal."""

import sys
import httpx

# Update this with the URL output by 'modal deploy modal_chatterbox.py'
MODAL_ENDPOINT = sys.argv[1] if len(sys.argv) > 1 else "https://<your-username>--oyi-chatterbox-turbo-chatterboxservice-tts.modal.run"

sample_text = (
    "Hey Sir Mario! [chuckle] I just finished porting the voice engine to Chatterbox Turbo. "
    "It's super lightweight, much cheaper on Modal, and I can express emotions naturally! [laugh]"
)

print(f"Synthesizing: \"{sample_text}\"")
print(f"Target: {MODAL_ENDPOINT}")

try:
    response = httpx.post(
        MODAL_ENDPOINT,
        json={"text": sample_text},
        timeout=60.0,
    )
    if response.status_code == 200:
        with open("chatterbox_test.mp3", "wb") as f:
            f.write(response.content)
        print("Success! Saved expressive audio to chatterbox_test.mp3.")
    else:
        print(f"Failed with status {response.status_code}: {response.text}")
except Exception as e:
    print(f"Connection error: {e}")
