"""Long-term memory (Mem0): recall/save/preference tools, plus capture of preference turns."""

from __future__ import annotations

import asyncio
from typing import Any

from oche.core import Context
from oche.loader import prompt_file

name = "memory"

LABELS = {"recall_memory": "Recalling memory", "save_memory": "Saving to memory", "update_preference": "Updating preferences"}


def apply(ctx: Context, config: dict[str, Any]) -> None:
    import memory

    ctx.prompt(config.get("prompt") or prompt_file("memory"))
    for tool in memory.MEMORY_TOOLS:
        ctx.tool(tool, label=LABELS.get(tool.name, ""))

    async def remember(turn: Any) -> None:
        # Only embed and extract when the user explicitly shared preference/biographical info.
        route = turn.state.get("route")
        if getattr(route, "intent", None) == "preference" and turn.text.strip():
            await asyncio.to_thread(memory.add_memory, turn.input, turn.text, turn.thread_id)

    ctx.on("turn/end", remember)
