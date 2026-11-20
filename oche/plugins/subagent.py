"""Background subagents: the model hands off long work and keeps chatting.

A subagent is just another ``agent.run`` on its own thread with a pinned model
and without the ``spawn_subagent`` tool. When it finishes, the result is sent
out with ``ctx.emit("notify", target, title, text)``; a frontend plugin
(Telegram, CLI, ...) decides how to deliver it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from oche.core import Context
from oche.plugins.agent import current_turn

name = "subagent"
inject = ["agent"]

LOGGER = logging.getLogger("oche.subagent")

PROMPT = """Background work: `spawn_subagent`.
- For long or deep tasks (multi-source research, in-depth reports, multi-file code, deep analysis), or when the
  user asks for background work, call `spawn_subagent` with a clear `task_name` and complete `instructions`,
  then tell the user it is running and they can keep chatting.
- Answer quick questions and routine tasks yourself."""

BRIEF = """You are working autonomously in the background on: {task}
Work thoroughly with your tools, then reply with the finished, well-structured deliverable,
addressed directly to the user. Do not mention that you are a subagent."""


def apply(ctx: Context, config: dict[str, Any]) -> None:
    model = config.get("model") or None

    async def work(target: str, task: str, instructions: str) -> None:
        thread_id = f"subagent-{uuid.uuid4().hex[:8]}"
        try:
            text = await ctx.agent.chat(
                instructions,
                thread_id,
                model=model,
                system_extra=BRIEF.format(task=task),
                exclude_tools={"spawn_subagent"},
            )
        except Exception:
            LOGGER.exception("subagent %r failed", task)
            text = f"⚠️ The background task '{task}' hit an error. Please try asking again."
        finally:
            ctx.agent.history.pop(thread_id, None)
        await ctx.emit("notify", target, task, text)

    ctx.prompt(PROMPT)

    @ctx.tool(label="Spinning up a subagent")
    def spawn_subagent(task_name: str, instructions: str) -> str:
        """Start an autonomous background subagent for long research, coding or analysis. Returns immediately;
        the finished report is delivered to the user when done."""
        turn = current_turn()
        if turn is None:
            return "Cannot start a subagent outside a conversation."
        ctx.task(work(turn.thread_id, task_name.strip(), instructions.strip()))
        return (
            f"Background subagent started for '{task_name}'. Tell the user it is running and that the report "
            "will arrive when it is done."
        )
