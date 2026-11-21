"""Voice notes: synthesize speech (Chatterbox/CosyVoice) and send it to Telegram."""

from __future__ import annotations

from typing import Any

from oche.core import Context
from oche.loader import prompt_file

name = "voice"


def apply(ctx: Context, config: dict[str, Any]) -> None:
    from voice import VOICE_TOOLS

    ctx.prompt(config.get("prompt") or prompt_file("voice"), order=80)
    for tool in VOICE_TOOLS:
        ctx.tool(tool, label="Recording a voice note")
