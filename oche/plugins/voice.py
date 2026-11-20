"""Voice notes: synthesize speech (Chatterbox/CosyVoice) and send it to Telegram."""

from __future__ import annotations

from typing import Any

from oche.core import Context
from oche.langchain import from_langchain

name = "voice"

PROMPT = """Voice notes: `send_voice_note`.
- ONLY call it when the user explicitly asks for a voice note, voice/audio message, or a spoken reply.
  Ordinary conversation is always answered in text.
- Allowed paralinguistic tags (no others): [laugh] [chuckle] [gasp] [cough] [sigh] [groan] [sniff] [shush]
  [clear throat] [whispering] [angry] [fear] [surprised] [crying] [happy] [sarcastic] [dramatic]
  [narration] [advertisement].
- Emotion tags go at the START of the sentence they affect; action tags go inline. Never stack tags.
  Use 1-3 tags per note. Match `mood` to the dominant emotion (cheerful / angry / normal).
- Example: send_voice_note(text="[happy] Congratulations! The deployment went out cleanly [chuckle], well done.",
  mood="cheerful", event="task_done", caption="Deployment celebration")
- Afterwards reply briefly ("Sent you a voice note above!") without repeating its text."""


def apply(ctx: Context, config: dict[str, Any]) -> None:
    from voice import VOICE_TOOLS

    ctx.prompt(PROMPT)
    for lc_tool in VOICE_TOOLS:
        ctx.tool(from_langchain(lc_tool, "Recording a voice note"))
