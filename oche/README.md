# Oche core

Everything is a plugin (the [Cordis](https://github.com/cordiverse/cordis) idea), and the core stays
tiny (the [pi](https://github.com/badlogic/pi-mono) idea). Two files hold all of the machinery:

- `core.py`: the plugin context. Plugins, services with `inject`, events, tools, prompt sections,
  background tasks, and automatic cleanup when a plugin unloads.
- `plugins/agent.py`: the agent loop. It sends the prompt, streams text, runs tool calls and repeats.

Everything else is a plugin listed in `../oche.toml`, loaded top to bottom.

## A plugin

```python
# myplugin.py
name = "dice"
inject = ["agent"]          # optional: wait until these services exist

def apply(ctx, config):
    ctx.prompt("You can roll dice with `roll`.")

    @ctx.tool(label="Rolling dice")
    def roll(sides: int = 6) -> str:
        """Roll one die."""
        import random
        return str(random.randint(1, sides))

    ctx.on("turn/end", lambda turn: print("replied:", turn.text))
```

Add `[plugins.myplugin]` to `oche.toml`. When the plugin unloads, or a service it injects goes away,
its tool, prompt section and listeners are removed automatically.

## The context API

| Call | What it does |
| --- | --- |
| `ctx.plugin(p, config)` | load a child plugin; returns a scope with `.dispose()` |
| `ctx.provide(name, value)` / `ctx.<name>` | expose or read a service |
| `ctx.on(event, fn)` | listen; `emit` calls all, `bail` returns the first non-`None`, `pipe` threads a value through |
| `ctx.tool(...)` | register a model-callable tool (JSON Schema inferred from type hints) |
| `ctx.prompt(text_or_fn, order=)` | contribute a system-prompt section |
| `ctx.task(coro)` | background work, cancelled on unload |
| `ctx.effect(fn)` | any other cleanup |

## Events the agent fires

- `turn/start(turn)`: rewrite `turn.prompt`, set `turn.model`, append `turn.context` lines.
- `turn/text(text, turn)`: a pipe for every streamed text delta (privacy restores aliases here).
- `turn/end(turn)`: `turn.text` holds the final reply.
- `notify(target, title, text)`: a plugin wants to tell the user something (subagent results).

## Services

| Service | Provided by | Shape |
| --- | --- | --- |
| `model` | `openai` | `stream(messages, tools, model)`, `complete(messages, model)` |
| `agent` | `agent` | `run(text, thread_id)` yields events; `chat(...)` returns text |

## Run

```sh
python -m oche                    # terminal chat with the plugins in oche.toml
OCHE_ENGINE=core python TG.py     # the Telegram bot on the new core
python test_oche.py               # tests (no network)
```
