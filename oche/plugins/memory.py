"""Long-term memory (Mem0) as tools, plus automatic capture of preference turns."""

from __future__ import annotations

import asyncio
from typing import Any

from oche.core import Context
from oche.langchain import from_langchain

name = "memory"

PROMPT = """Long-term memory: `recall_memory`, `save_memory`, `update_preference`.
- Call `recall_memory` only when the user asks about earlier conversations, personal history, or past project details.
- Call `save_memory` / `update_preference` only when the user shares a durable fact or preference ("remember that I...").
- Never save passing chit-chat. Never use web search for the user's own background."""

LABELS = {"recall_memory": "Recalling memory", "save_memory": "Saving to memory", "update_preference": "Updating preferences"}


def apply(ctx: Context, config: dict[str, Any]) -> None:
    import memory

    ctx.prompt(PROMPT)
    for lc_tool in memory.MEMORY_TOOLS:
        ctx.tool(from_langchain(lc_tool, LABELS.get(lc_tool.name, "")))

    async def remember(turn: Any) -> None:
        route = turn.state.get("route") or {}
        if config.get("capture_preferences", True) and route.get("intent") == "preference" and turn.text.strip():
            await asyncio.to_thread(memory.add_memory, turn.input, turn.text, turn.thread_id)

    ctx.on("turn/end", remember)
