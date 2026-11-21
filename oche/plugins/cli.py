"""A terminal chat frontend: ``python -m oche``."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from oche.core import Context

name = "cli"
inject = ["agent"]


def apply(ctx: Context, config: dict[str, Any]) -> None:
    thread_id = config.get("thread_id", "cli")

    async def repl() -> None:
        print("oche ready. Ctrl-D to quit.", file=sys.stderr)
        while True:
            try:
                line = await asyncio.to_thread(input, "› ")
            except EOFError:
                break
            if not line.strip():
                continue
            async for event in ctx.agent.astream_events(line, thread_id):
                if event["type"] == "token":
                    print(event["text"], end="", flush=True)
                elif event["type"] == "tool" and event["state"] == "start":
                    print(f"\n[{event['label']}…]", file=sys.stderr, flush=True)
            print()
        await ctx.emit("exit")

    ctx.on("ready", lambda: ctx.task(repl()))
