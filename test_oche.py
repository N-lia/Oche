#!/usr/bin/env python3
"""Tests for the Oche plugin core and the Deep Agents engine plugin (no network).

Run:
    .venv/bin/python test_oche.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types

os.environ.setdefault("NEBIUS_API_KEY", "test-key")
os.environ.setdefault("TAVILY_API_KEY", "test-key")

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from oche import App, create_app
from oche.loader import prompt_file
from oche.plugins import agent as agent_plugin
from oche.plugins import clock, privacy


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ core


def test_inject_waits_for_service_and_tears_down() -> None:
    app = App()
    log: list[str] = []

    def ping() -> str:
        """Ping."""
        return "pong"

    def consumer(ctx, config):
        log.append("start")
        ctx.tool(ping, label="Pinging")
        ctx.effect(lambda: log.append("stop"))

    consumer.inject = ["db"]
    scope = app.plugin(consumer)
    assert not scope.active and log == [] and "ping" not in app.tools

    provider = app.plugin(lambda ctx, cfg: ctx.provide("db", object()))
    assert scope.active and log == ["start"] and app.tools["ping"] is ping and app.label("ping") == "Pinging"

    provider.dispose()  # service gone -> dependent stops, its tool disappears
    assert not scope.active and log == ["start", "stop"] and "ping" not in app.tools

    app.plugin(lambda ctx, cfg: ctx.provide("db", object()))  # back -> restarts
    assert scope.active and log == ["start", "stop", "start"]

    scope.dispose()
    assert scope.disposed and "ping" not in app.tools and app.label("ping") == "ping"


def test_child_plugins_unload_with_parent() -> None:
    app = App()

    def child(ctx, config):
        ctx.on("hello", lambda: None)

    def parent(ctx, config):
        ctx.plugin(child)

    scope = app.plugin(parent)
    assert len(scope.children) == 1 and app._root.listeners["hello"]
    scope.dispose()
    assert app._root.listeners["hello"] == []


def test_events_emit_bail_pipe() -> None:
    app = App()
    seen: list[int] = []
    app.on("e", lambda x: seen.append(x))

    async def async_handler(x):
        seen.append(x * 10)

    app.on("e", async_handler)
    app.on("ask", lambda: None)
    app.on("ask", lambda: "first")
    app.on("ask", lambda: "second")
    app.on("text", lambda s: s.upper())
    app.on("text", lambda s: None)
    app.on("text", lambda s: s + "!")

    async def go():
        await app.emit("e", 2)
        return await app.bail("ask"), await app.pipe("text", "hi")

    assert run(go()) == ("first", "HI!")
    assert app.pipe_sync("text", "yo") == "YO!"
    assert seen == [2, 20]


def test_prompt_sections_are_ordered_and_versioned() -> None:
    app = App()
    v0 = app.version

    def plugin(ctx, config):
        ctx.prompt("late", order=90)
        ctx.prompt("  early\n  second line", order=0)
        ctx.prompt("middle")

    scope = app.plugin(plugin)
    assert app.system_prompt() == "early\nsecond line\n\nmiddle\n\nlate"
    v1 = app.version
    assert v1 > v0
    scope.dispose()
    assert app.system_prompt() == "" and app.version > v1


def test_loader_resolves_disables_and_idents() -> None:
    fake = types.ModuleType("fake_oche_plugin")
    loaded: list = []
    fake.apply = lambda ctx, config: loaded.append(config)
    sys.modules["fake_oche_plugin"] = fake
    app = create_app({"plugins": {"clock": {}, "~telegram": {}, "fake_oche_plugin": {"n": 1}, "fake_oche_plugin:b": {}}})
    assert loaded == [{"n": 1}, {}]
    assert app.system_prompt() == prompt_file("clock").strip()  # telegram disabled


# ------------------------------------------------------------------ the real config


def test_default_config_loads_every_plugin() -> None:
    app = create_app()
    states = {scope.name: scope.active for scope in app._root.scopes}
    assert all(states.values()), states
    assert {"llm", "agent", "privacy", "clock", "router", "workspace", "memory", "tavily", "subagent", "voice", "telegram"} <= set(states)
    assert {"tavily_search", "recall_memory", "spawn_subagent", "send_voice_note"} <= set(app.tools)
    assert app.get("backend") is not None


def test_system_prompt_keeps_every_original_instruction() -> None:
    prompt = create_app().system_prompt()
    assert prompt.startswith("You are OYI, Sir Mario's personal AI assistant.")
    for marker in (
        "Temporal Awareness", "Subagent Delegation Policy", "Web Access & Tavily Tool Selection Guide",
        "Long-term memory & preferences", "Response policy", "File-writing policy",
        "Voice Notes & Spoken Audio Policy", "Reply formatting — Telegram HTML (STRICT)",
    ):
        assert marker in prompt, marker
    assert prompt.index("Temporal Awareness") < prompt.index("Subagent Delegation") < prompt.index("Reply formatting")


# ------------------------------------------------------------------ the engine


class ScriptedModel(GenericFakeChatModel):
    """A fake chat model that accepts tools and replays scripted messages."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        # Emit each scripted message as one chunk, tool calls included.
        message = next(self.messages)
        chunk = AIMessageChunk(
            content=message.content,
            tool_call_chunks=[
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(message.tool_calls)
            ],
        )
        yield ChatGenerationChunk(message=chunk)


def engine_app(messages: list[AIMessage], *extra_plugins) -> tuple[App, list]:
    model = ScriptedModel(messages=iter(messages))
    requested: list = []

    class FakeModels:
        def get(self, model_name=None, **overrides):
            requested.append(model_name)
            return model

    app = App()
    app.plugin(lambda ctx, cfg: ctx.provide("llm", FakeModels()))
    app.plugin(agent_plugin, {"prompt": "BASE"})
    for plugin in extra_plugins:
        app.plugin(plugin)
    return app, requested


def test_engine_runs_plugin_tools_and_streams_events() -> None:
    calls: list[tuple[str, str]] = []

    def tools(ctx, config):
        def weather(city: str) -> str:
            """Weather for a city."""
            calls.append((city, str(agent_plugin_scope())))
            return f"sunny in {city}"

        ctx.tool(weather, label="Checking weather")

    def agent_plugin_scope():
        import memory

        return memory._CURRENT_USER_ID.get()

    app, _ = engine_app(
        [
            AIMessage(content="", tool_calls=[{"name": "weather", "args": {"city": "Lagos"}, "id": "c1"}]),
            AIMessage(content="It is sunny."),
        ],
        tools,
    )

    async def go():
        return [e async for e in app.agent.astream_events("weather?", "chat7")]

    events = run(go())
    assert events[0]["type"] == "status" and events[-1] == {"type": "done"}
    tool_events = [(e["name"], e["label"], e["state"]) for e in events if e["type"] == "tool"]
    assert ("weather", "Checking weather", "start") in tool_events
    assert ("weather", "Checking weather", "done") in tool_events
    assert "".join(e["text"] for e in events if e["type"] == "token") == "It is sunny."
    assert calls == [("Lagos", "chat7")]  # tools see the chat id, as the legacy tools expect


def test_engine_turn_hooks_privacy_and_model_choice() -> None:
    seen: list = []

    def pick_model(ctx, config):
        def start(turn):
            turn.model = "tiny"
            seen.append(turn.message()["content"])

        ctx.on("turn/start", start)
        ctx.on("turn/end", lambda turn: seen.append(turn.text))

    app, requested = engine_app([AIMessage(content="noted ")], privacy, clock, pick_model)
    reply = run(_collect(app, "mail a.person@example.com"))
    assert requested == ["tiny"]
    assert seen[0].startswith("[Current Date & Time: ") and "a.person@example.com" not in seen[0]
    assert "<EMAIL_" in seen[0]
    assert reply == "noted " and seen[-1] == "noted "


def test_privacy_restores_aliases_in_streamed_text() -> None:
    app = App()
    app.plugin(privacy)
    turn = agent_plugin.Turn(thread_id="me", input="write to a.person@example.com", prompt="write to a.person@example.com")
    run(app.emit("turn/start", turn))
    alias = turn.prompt.split()[-1]
    assert alias.startswith("<EMAIL_")
    assert app.pipe_sync("turn/text", f"Sent to {alias}.", turn) == "Sent to a.person@example.com."


def test_engine_rebuilds_when_plugins_change() -> None:
    app, _ = engine_app([AIMessage(content="one"), AIMessage(content="two")])
    first = app.agent.agent_for(None)
    assert app.agent.agent_for(None) is first

    def extra(ctx, config):
        ctx.prompt("new rule")

    scope = app.plugin(extra)
    second = app.agent.agent_for(None)
    assert second is not first
    scope.dispose()
    assert app.agent.agent_for(None) is not second


def test_engine_sync_chat() -> None:
    app, _ = engine_app([AIMessage(content="hello there")], clock)
    assert app.agent.chat("hi", "t1") == "hello there"


async def _collect(app: App, text: str) -> str:
    return "".join([chunk async for chunk in app.agent.astream(text, "me")])


def main() -> int:
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {exc!r}")
            import traceback

            traceback.print_exc()
        else:
            print(f"ok   {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
