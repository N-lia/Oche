"""The agent loop, as a plugin (pi-style: small, plain, inspectable).

Provides the ``agent`` service. One call to ``agent.run(text, thread_id)``:

1. builds a ``Turn`` and fires ``turn/start`` so plugins can rewrite the
   prompt, pick a model, or add context lines (router, clock, privacy...);
2. sends history + system prompt + tools to the ``model`` service;
3. streams text (piped through ``turn/text``), runs any tool calls, feeds the
   results back, and repeats until the model answers without tools;
4. stores the exchange and fires ``turn/end``.

It yields the same event dicts the WebApp already consumes:
``status``, ``tool`` (start/done), ``token``, and a final ``done``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from oche.core import Context

name = "agent"
inject = ["model"]

LOGGER = logging.getLogger("oche.agent")

_CURRENT_TURN: ContextVar[Turn | None] = ContextVar("oche_current_turn", default=None)

BASE_PROMPT = """You are a helpful personal assistant. Answer directly and concisely.
Use tools only when they genuinely help; never expose raw tool output or internal reasoning."""


def current_turn() -> Turn | None:
    """The turn a tool is running inside (thread id, state, ...)."""
    return _CURRENT_TURN.get()


@dataclass
class Turn:
    thread_id: str
    input: str  # what the user sent, untouched
    prompt: str  # what the model will see; plugins may rewrite it
    model: str | None = None  # None = provider default
    context: list[str] = field(default_factory=list)  # header lines prefixed to the prompt
    state: dict[str, Any] = field(default_factory=dict)  # per-turn scratch space for plugins
    text: str = ""  # final visible reply
    exclude_tools: set[str] = field(default_factory=set)
    system_extra: str = ""


class Agent:
    def __init__(self, ctx: Context, config: dict[str, Any]) -> None:
        self.ctx = ctx
        self.max_steps = int(config.get("max_steps", 12))
        self.max_history = int(config.get("max_history", 60))
        self.history: dict[str, list[dict[str, Any]]] = {}

    def tool_label(self, name: str) -> str:
        tool = self.ctx.tools.get(name)
        return (tool.label if tool else "") or name

    async def chat(self, text: str, thread_id: str = "me", **options: Any) -> str:
        """Run a turn and return only the final reply text."""
        parts = [event["text"] async for event in self.run(text, thread_id, **options) if event["type"] == "token"]
        return "".join(parts)

    async def run(
        self,
        text: str,
        thread_id: str = "me",
        *,
        model: str | None = None,
        system_extra: str = "",
        exclude_tools: set[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        ctx = self.ctx
        turn = Turn(
            thread_id=thread_id,
            input=text,
            prompt=text,
            model=model,
            system_extra=system_extra,
            exclude_tools=set(exclude_tools or ()),
        )
        await ctx.emit("turn/start", turn)
        yield {"type": "status", "phase": "thinking", "text": "Thinking…"}

        history = self.history.setdefault(thread_id, [])
        content = "\n".join(turn.context) + "\n\n" + turn.prompt if turn.context else turn.prompt
        new_messages: list[dict[str, Any]] = [{"role": "user", "content": content}]

        system = await ctx.system_prompt(turn)
        if turn.system_extra:
            system = f"{system}\n\n{turn.system_extra}".strip()
        tools = {n: t for n, t in ctx.tools.items() if n not in turn.exclude_tools}
        schemas = [t.schema() for t in tools.values()]
        visible: list[str] = []

        for _step in range(self.max_steps):
            messages = [{"role": "system", "content": system}, *history, *new_messages]
            text_parts: list[str] = []
            calls: list[dict[str, Any]] = []
            async for event in ctx.model.stream(messages=messages, tools=schemas, model=turn.model):
                if event["type"] == "text":
                    text_parts.append(event["text"])
                    shown = await ctx.pipe("turn/text", event["text"], turn)
                    if shown:
                        visible.append(shown)
                        yield {"type": "token", "text": shown}
                elif event["type"] == "tool_call":
                    calls.append(event)

            assistant: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
            if calls:
                assistant["tool_calls"] = [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in calls
                ]
            new_messages.append(assistant)
            if not calls:
                break

            for call in calls:
                label = self.tool_label(call["name"])
                yield {"type": "tool", "name": call["name"], "label": label, "state": "start"}
                result = await self._run_tool(tools, call, turn)
                new_messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                yield {"type": "tool", "name": call["name"], "label": label, "state": "done"}
        else:
            LOGGER.warning("turn hit max_steps=%s on thread %s", self.max_steps, thread_id)

        history.extend(new_messages)
        if len(history) > self.max_history:
            del history[: len(history) - self.max_history]
            # Never start history on an orphaned tool result.
            while history and history[0]["role"] != "user":
                history.pop(0)
        turn.text = "".join(visible)
        await ctx.emit("turn/end", turn)
        yield {"type": "done"}

    async def _run_tool(self, tools: dict[str, Any], call: dict[str, Any], turn: Turn) -> str:
        tool = tools.get(call["name"])
        if tool is None:
            return f"Error: unknown tool {call['name']!r}."
        try:
            args = json.loads(call["arguments"] or "{}")
        except json.JSONDecodeError as exc:
            return f"Error: tool arguments were not valid JSON ({exc})."
        token = _CURRENT_TURN.set(turn)
        try:
            return await tool(**args)
        except Exception as exc:  # noqa: BLE001 - errors go back to the model
            LOGGER.exception("tool %s failed", call["name"])
            return f"Error running {call['name']}: {exc}"
        finally:
            _CURRENT_TURN.reset(token)


def apply(ctx: Context, config: dict[str, Any]) -> None:
    prompt = config.get("prompt", BASE_PROMPT)
    if prompt:
        ctx.prompt(prompt, order=0)
    ctx.provide("agent", Agent(ctx, config))
