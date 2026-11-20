#!/usr/bin/env python3
"""Tests for the Oche plugin core and its built-in plugins (no network).

Run:
    .venv/bin/python test_oche.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types

from oche import App, Tool, create_app
from oche.plugins import agent as agent_plugin
from oche.plugins import router, subagent, workspace


# ------------------------------------------------------------------ helpers


class FakeModel:
    """Scripted model: each call to stream() pops the next list of events."""

    def __init__(self, script: list[list[dict]]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    async def stream(self, *, messages, tools=None, model=None):
        self.calls.append({"messages": messages, "tools": tools, "model": model})
        for event in self.script.pop(0) if self.script else [{"type": "text", "text": "ok"}]:
            yield event

    async def complete(self, messages, model=None, **extra):
        return '{"intent": "analysis", "complexity": "serious", "confidence": 0.9}'


def fake_model_plugin(model: FakeModel):
    def apply(ctx, config):
        ctx.provide("model", model)

    apply.name = "fake-model"
    return apply


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ core


def test_inject_waits_for_service_and_tears_down() -> None:
    app = App()
    log: list[str] = []

    def consumer(ctx, config):
        log.append("start")
        ctx.tool(Tool("ping", "ping", {"type": "object", "properties": {}}, lambda: "pong"))
        ctx.effect(lambda: log.append("stop"))

    consumer.inject = ["db"]
    scope = app.plugin(consumer)
    assert not scope.active and log == [] and "ping" not in app.tools

    provider = app.plugin(lambda ctx, cfg: ctx.provide("db", object()))
    assert scope.active and log == ["start"] and "ping" in app.tools

    provider.dispose()  # service gone -> dependent stops, its tool disappears
    assert not scope.active and log == ["start", "stop"] and "ping" not in app.tools

    app.plugin(lambda ctx, cfg: ctx.provide("db", object()))  # back -> restarts
    assert scope.active and log == ["start", "stop", "start"]

    scope.dispose()
    assert scope.disposed and "ping" not in app.tools


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
    assert seen == [2, 20]


def test_tool_decorator_infers_schema() -> None:
    app = App()

    @app.tool(label="Adding")
    def add(a: int, b: int = 2, note: str = "") -> str:
        """Add two numbers."""
        return str(a + b)

    tool = app.tools["add"]
    assert tool.description == "Add two numbers." and tool.label == "Adding"
    assert tool.parameters["required"] == ["a"]
    assert tool.parameters["properties"]["a"]["type"] == "integer"
    assert run(tool(a=1)) == "3"


def test_prompt_sections_are_ordered_and_dynamic() -> None:
    app = App()
    app.prompt("late", order=90)
    app.prompt("early", order=0)
    app.prompt(lambda turn: f"for {turn}")
    app.prompt(lambda turn: None)
    assert run(app.system_prompt("t1")) == "early\n\nfor t1\n\nlate"


# ------------------------------------------------------------------ agent loop


def test_agent_runs_tools_and_keeps_history() -> None:
    model = FakeModel([
        [{"type": "text", "text": "Let me check. "},
         {"type": "tool_call", "id": "c1", "name": "weather", "arguments": json.dumps({"city": "Lagos"})}],
        [{"type": "text", "text": "It is sunny."}],
    ])
    app = App()
    app.plugin(fake_model_plugin(model))
    app.plugin(agent_plugin, {"prompt": "BASE"})

    seen_turns: list = []

    @app.tool(label="Checking weather")
    def weather(city: str) -> str:
        """Weather for a city."""
        seen_turns.append(agent_plugin.current_turn().thread_id)
        return f"sunny in {city}"

    async def go():
        await app.start()
        return [e async for e in app.agent.run("weather?", "t1")]

    events = run(go())
    kinds = [(e["type"], e.get("state")) for e in events]
    assert kinds == [("status", None), ("token", None), ("tool", "start"), ("tool", "done"), ("token", None), ("done", None)]
    assert events[2]["label"] == "Checking weather"
    assert seen_turns == ["t1"]
    second = model.calls[1]["messages"]
    assert second[0] == {"role": "system", "content": "BASE"}
    assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": "sunny in Lagos"}
    assert [m["role"] for m in app.agent.history["t1"]] == ["user", "assistant", "tool", "assistant"]


def test_agent_reports_tool_errors_to_model() -> None:
    model = FakeModel([
        [{"type": "tool_call", "id": "c1", "name": "boom", "arguments": "{}"},
         {"type": "tool_call", "id": "c2", "name": "missing", "arguments": "{}"},
         {"type": "tool_call", "id": "c3", "name": "boom", "arguments": "{not json"}],
        [{"type": "text", "text": "sorry"}],
    ])
    app = App()
    app.plugin(fake_model_plugin(model))
    app.plugin(agent_plugin)

    @app.tool
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("kaput")

    reply = run(app.agent.chat("go"))
    results = [m["content"] for m in model.calls[1]["messages"] if m["role"] == "tool"]
    assert reply == "sorry"
    assert "kaput" in results[0] and "unknown tool" in results[1] and "not valid JSON" in results[2]


def test_turn_hooks_rewrite_prompt_model_and_text() -> None:
    model = FakeModel([[{"type": "text", "text": "hello <X>"}]])
    app = App()
    app.plugin(fake_model_plugin(model))
    app.plugin(agent_plugin)

    def hooks(ctx, config):
        def start(turn):
            turn.prompt = turn.prompt.replace("secret", "<X>")
            turn.context.append("[ctx]")
            turn.model = "tiny"

        ctx.on("turn/start", start)
        ctx.on("turn/text", lambda text, turn: text.replace("<X>", "secret"))

    app.plugin(hooks)
    reply = run(app.agent.chat("say secret"))
    assert reply == "hello secret"
    assert model.calls[0]["model"] == "tiny"
    assert model.calls[0]["messages"][-1]["content"] == "[ctx]\n\nsay <X>"


def test_privacy_plugin_round_trip() -> None:
    model = FakeModel([])

    async def echo(*, messages, tools=None, model=None):
        yield {"type": "text", "text": "Got " + messages[-1]["content"]}

    model.stream = echo
    app = App()
    app.plugin(fake_model_plugin(model))
    app.plugin(agent_plugin)
    from oche.plugins import privacy

    app.plugin(privacy)
    reply = run(app.agent.chat("mail me at a.person@example.com"))
    assert reply == "Got mail me at a.person@example.com"
    assert "example.com" not in json.dumps(app.agent.history["me"])


# ------------------------------------------------------------------ plugins


def test_router_heuristics_and_classifier() -> None:
    assert router.heuristic("hello!")["reason"] == "greeting"
    assert router.heuristic("what is the bitcoin price today")["intent"] == "current_information"
    assert router.heuristic("thanks a lot")["complexity"] == "routine"
    assert router.heuristic("please write a detailed comparison of three databases for my startup") is None
    assert router.parse_decision('sure: {"intent": "coding", "complexity": "standard", "confidence": 2}') == {
        "intent": "coding", "complexity": "standard", "confidence": 1.0, "reason": "classifier"
    }
    tiers = {"routine": "r", "standard": "s", "serious": "x", "search": "q"}
    assert router.choose({"intent": "current_information", "complexity": "standard", "confidence": 0.9}, tiers) == "q"
    assert router.choose({"intent": "coding", "complexity": "standard", "confidence": 0.5}, tiers) == "x"

    model = FakeModel([[{"type": "text", "text": "done"}]])
    app = App()
    app.plugin(fake_model_plugin(model))
    app.plugin(agent_plugin)
    app.plugin(router, {"routine": "r", "standard": "s", "serious": "x", "classifier": "c"})
    run(app.agent.chat("please write a detailed comparison of three databases for my startup"))
    assert model.calls[0]["model"] == "x"


def test_workspace_tools_stay_inside_root() -> None:
    with tempfile.TemporaryDirectory() as root:
        app = App()
        app.plugin(workspace, {"root": root})
        tools = app.tools
        assert run(tools["write"](path="notes/a.txt", content="one two")).startswith("Wrote")
        assert run(tools["edit"](path="notes/a.txt", old="two", new="three")) == "Edited notes/a.txt."
        assert run(tools["read"](path="notes/a.txt")) == "one three"
        assert run(tools["ls"](path="notes")) == "a.txt"
        assert "exactly once" in run(tools["edit"](path="notes/a.txt", old="zzz", new="y"))
        try:
            run(tools["read"](path="../../etc/passwd"))
        except ValueError as exc:
            assert "outside the workspace" in str(exc)
        else:
            raise AssertionError("escaped the workspace")


def test_subagent_runs_in_background_and_notifies() -> None:
    model = FakeModel([
        [{"type": "tool_call", "id": "c1", "name": "spawn_subagent",
          "arguments": json.dumps({"task_name": "Report", "instructions": "Write it"})}],
        [{"type": "text", "text": "Started it."}],
        [{"type": "text", "text": "The report."}],
    ])
    app = App()
    app.plugin(fake_model_plugin(model))
    app.plugin(agent_plugin)
    app.plugin(subagent, {"model": "big"})
    notes: list[tuple] = []
    done = asyncio.Event()

    def notify(target, title, text):
        notes.append((target, title, text))
        done.set()

    app.on("notify", notify)

    async def go():
        reply = await app.agent.chat("do the report", "chat42")
        await asyncio.wait_for(done.wait(), 2)
        return reply

    assert run(go()) == "Started it."
    assert notes == [("chat42", "Report", "The report.")]
    sub_call = model.calls[2]
    assert sub_call["model"] == "big"
    assert "spawn_subagent" not in [t["function"]["name"] for t in sub_call["tools"]]
    assert list(app.agent.history) == ["chat42"]


def test_loader_resolves_disables_and_idents() -> None:
    fake = types.ModuleType("fake_oche_plugin")
    loaded: list = []
    fake.apply = lambda ctx, config: loaded.append(config)
    sys.modules["fake_oche_plugin"] = fake
    app = create_app({"plugins": {
        "clock": {},
        "~workspace": {},
        "fake_oche_plugin": {"n": 1},
    }})
    assert loaded == [{"n": 1}]
    assert "read" not in app.tools  # workspace disabled
    assert app._root.prompts  # clock contributed a section


def test_openai_stream_parsing() -> None:
    try:
        from oche.plugins.openai import ToolCallBuffer, parse_sse
    except ImportError:  # httpx not installed in this environment
        print("skip openai parsing (httpx missing)")
        return
    lines = [
        'data: {"choices":[{"delta":{"content":"Hi"}}]}',
        "",
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"we","arguments":"{\\"c"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"ather","arguments":"ity\\": 1}"}}]}}]}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"ignored"}}]}',
    ]
    chunks = list(parse_sse(lines))
    assert len(chunks) == 3
    buffer = ToolCallBuffer()
    for chunk in chunks[1:]:
        buffer.add(chunk["choices"][0]["delta"]["tool_calls"])
    assert buffer.drain() == [{"type": "tool_call", "id": "a", "name": "weather", "arguments": '{"city": 1}'}]


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
