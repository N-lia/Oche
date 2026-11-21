"""The engine: a LangChain Deep Agent assembled from whatever the plugins registered.

Provides the ``agent`` service. Each turn:

1. builds a ``Turn`` and fires ``turn/start`` so plugins can rewrite the
   prompt, pick a model, or add context lines (privacy, clock, router...);
2. runs a Deep Agent whose tools and system prompt are every tool and prompt
   section currently registered (rebuilt automatically when plugins change);
3. pipes each streamed text chunk through ``turn/text``;
4. fires ``turn/end`` with the final reply.

The public methods match the old ``AgentService`` (``chat``, ``stream``,
``astream``, ``astream_events``, ``get_config``, ``assistant_text_chunk``),
so ``main.py``, the bot and the WebApp do not change.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from oche.core import Context

name = "agent"
inject = ["llm"]

LOGGER = logging.getLogger("oche.agent")


@dataclass
class Turn:
    thread_id: str
    input: str  # what the user sent, untouched
    prompt: str  # what the model will see; plugins may rewrite it
    model: str | None = None  # None = the llm plugin's default
    context: list[str] = field(default_factory=list)  # header lines prefixed to the prompt
    state: dict[str, Any] = field(default_factory=dict)  # per-turn scratch space for plugins
    text: str = ""  # final visible reply

    def message(self) -> dict[str, str]:
        content = "\n".join(self.context) + "\n\n" + self.prompt if self.context else self.prompt
        return {"role": "user", "content": content}


def _register_harness_profile() -> None:
    """Disable Deep Agents' blocking ``task`` subagent and dummy ``execute`` tool."""
    from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile

    register_harness_profile(
        "openai",
        HarnessProfile(
            excluded_tools=frozenset({"execute"}),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )


def _as_langchain(tool: Any) -> Any:
    from langchain_core.tools import BaseTool
    from langchain_core.tools import tool as make_tool

    return tool if isinstance(tool, BaseTool) else make_tool(tool)


class Agent:
    def __init__(self, ctx: Context, config: dict[str, Any]) -> None:
        from langgraph.checkpoint.memory import MemorySaver

        self.ctx = ctx
        self.checkpointer = MemorySaver()
        self._agents: dict[str, Any] = {}
        self._version = -1

    # Assembly -----------------------------------------------------------------

    def build(self, model_name: str | None = None, *, exclude: set[str] = frozenset(), **create: Any) -> Any:
        """A Deep Agent over the current plugin tools and prompt (uncached)."""
        from deepagents import create_deep_agent

        tools = [_as_langchain(t) for n, t in self.ctx.tools.items() if n not in exclude]
        backend = self.ctx.get("backend")
        if backend is not None:
            create.setdefault("backend", backend)
        create.setdefault("system_prompt", self.ctx.system_prompt())
        return create_deep_agent(model=self.ctx.llm.get(model_name), tools=tools, **create)

    def agent_for(self, model_name: str | None) -> Any:
        if self._version != self.ctx.version:  # a plugin added/removed a tool or prompt section
            self._agents.clear()
            self._version = self.ctx.version
        key = model_name or ""
        if key not in self._agents:
            self._agents[key] = self.build(model_name, checkpointer=self.checkpointer)
        return self._agents[key]

    # Turn helpers ---------------------------------------------------------------

    @staticmethod
    def get_config(thread_id: str = "me") -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def assistant_text_chunk(chunk: Any) -> str:
        if getattr(chunk, "type", "") not in {"AIMessage", "AIMessageChunk", "ai"}:
            return ""
        content = getattr(chunk, "content", "")
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "".join(
            str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )

    @staticmethod
    def _final_text(response: dict[str, Any]) -> str:
        last = response["messages"][-1]
        raw = getattr(last, "content", None)
        if raw is None and isinstance(last, dict):
            raw = last.get("content", "")
        if isinstance(raw, list):
            raw = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
        return str(raw or "")

    async def _start(self, text: str, thread_id: str) -> Turn:
        turn = Turn(thread_id=thread_id, input=text, prompt=text)
        await self.ctx.emit("turn/start", turn)
        return turn

    async def _end(self, turn: Turn) -> None:
        await self.ctx.emit("turn/end", turn)

    def _scope(self, thread_id: str) -> Any:
        """Tools read the current user/chat id from memory's context variable."""
        from memory import set_current_user_id

        return set_current_user_id(thread_id)

    @staticmethod
    def _unscope(token: Any) -> None:
        from memory import reset_current_user_id

        reset_current_user_id(token)

    # Public API (same shape as the old AgentService) ------------------------------

    def chat(self, user_input: str, thread_id: str = "me") -> str:
        turn = asyncio.run(self._start(user_input, thread_id))
        token = self._scope(thread_id)
        try:
            response = self.agent_for(turn.model).invoke({"messages": [turn.message()]}, config=self.get_config(thread_id))
        finally:
            self._unscope(token)
        turn.text = self.ctx.pipe_sync("turn/text", self._final_text(response), turn)
        asyncio.run(self._end(turn))
        return turn.text

    def stream(self, user_input: str, thread_id: str = "me"):
        turn = asyncio.run(self._start(user_input, thread_id))
        visible: list[str] = []
        token = self._scope(thread_id)
        try:
            for chunk, _ in self.agent_for(turn.model).stream(
                {"messages": [turn.message()]}, config=self.get_config(thread_id), stream_mode="messages"
            ):
                text = self.assistant_text_chunk(chunk)
                if text:
                    text = self.ctx.pipe_sync("turn/text", text, turn)
                    visible.append(text)
                    yield text
        finally:
            self._unscope(token)
        turn.text = "".join(visible)
        asyncio.run(self._end(turn))

    async def astream(self, user_input: str, thread_id: str = "me"):
        async for event in self.astream_events(user_input, thread_id):
            if event["type"] == "token":
                yield event["text"]

    async def astream_events(self, user_input: str, thread_id: str = "me"):
        """Structured stream for rich clients: ``status`` / ``tool`` / ``token`` events, then ``done``."""
        turn = await self._start(user_input, thread_id)
        visible: list[str] = []
        announced: set[str] = set()
        token = self._scope(thread_id)
        try:
            yield {"type": "status", "phase": "thinking", "text": "Thinking…"}
            async for chunk, _meta in self.agent_for(turn.model).astream(
                {"messages": [turn.message()]}, config=self.get_config(thread_id), stream_mode="messages"
            ):
                try:
                    if getattr(chunk, "type", "") == "tool":
                        tool_name = getattr(chunk, "name", None) or "tool"
                        yield {"type": "tool", "name": tool_name, "label": self.ctx.label(tool_name), "state": "done"}
                    else:
                        for call in getattr(chunk, "tool_calls", None) or []:
                            tool_name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
                            if tool_name and tool_name not in announced:
                                announced.add(tool_name)
                                yield {
                                    "type": "tool",
                                    "name": tool_name,
                                    "label": self.ctx.label(tool_name),
                                    "state": "start",
                                }
                except Exception:  # noqa: BLE001 - activity is best-effort
                    pass
                text = self.assistant_text_chunk(chunk)
                if text:
                    text = self.ctx.pipe_sync("turn/text", text, turn)
                    visible.append(text)
                    yield {"type": "token", "text": text}
        finally:
            self._unscope(token)
        turn.text = "".join(visible)
        await self._end(turn)
        yield {"type": "done"}


def apply(ctx: Context, config: dict[str, Any]) -> None:
    _register_harness_profile()
    from oche.loader import prompt_file

    ctx.prompt(config.get("prompt") or prompt_file("agent"), order=0)
    ctx.provide("agent", Agent(ctx, config))
