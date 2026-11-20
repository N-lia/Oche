"""Tell the model what time it is on every turn."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from oche.core import Context

name = "clock"

PROMPT = """Every message starts with the live [Current Date & Time: ...]. Treat it as the present.
Your training data is older: never guess about events, prices, releases or news after it."""


def apply(ctx: Context, config: dict[str, Any]) -> None:
    ctx.prompt(PROMPT, order=10)

    def stamp(turn: Any) -> None:
        now = datetime.now(timezone.utc).strftime("%A, %B %d, %Y at %H:%M:%S UTC")
        turn.context.insert(0, f"[Current Date & Time: {now}]")

    ctx.on("turn/start", stamp)
