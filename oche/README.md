# Oche plugins

Everything is a plugin (the [Cordis](https://github.com/cordiverse/cordis) idea), and the core stays
tiny. The engine is still a LangChain **Deep Agent**: the `agent` plugin builds it from whatever
tools and prompt sections the other plugins have registered, and rebuilds it when that changes.

- `core.py`: the plugin context. It handles plugins, services with `inject`, events, tools, prompt
  sections, background tasks, and cleanup when a plugin unloads. It has no LangChain import.
- `plugins/agent.py`: the Deep Agent engine. Same API as the old `AgentService`.
- `prompts/<plugin>.txt`: each plugin's slice of the system prompt.
- `../oche.toml`: which plugins load, in order. Prefix a name with `~` to turn it off.

## A plugin

```python
# dice.py
name = "dice"

def apply(ctx, config):
    ctx.prompt("You can roll dice with `roll`.")

    def roll(sides: int = 6) -> str:
        """Roll one die."""          # plain functions become LangChain tools
        import random
        return str(random.randint(1, sides))

    ctx.tool(roll, label="Rolling dice")   # LangChain tools work as-is too
    ctx.on("turn/end", lambda turn: print("replied:", turn.text))
```

Add `[plugins.dice]` to `oche.toml`. Unloading the plugin removes its tool, prompt and listeners,
and the Deep Agent is rebuilt without them on the next turn.

## The context API

| Call | What it does |
| --- | --- |
| `ctx.plugin(p, config)` | load a child plugin; returns a scope with `.dispose()` |
| `ctx.provide(name, value)` / `ctx.<name>` | expose or read a service |
| `ctx.on(event, fn)` | listen; `emit` calls all, `bail` returns the first non-`None`, `pipe` threads a value through |
| `ctx.tool(tool, label=)` | give the agent a tool |
| `ctx.prompt(text, order=)` | add a system-prompt section (lower `order` first) |
| `ctx.task(coro)` | background work, cancelled on unload |
| `ctx.effect(fn)` | any other cleanup |

## Turn events

- `turn/start(turn)`: rewrite `turn.prompt`, set `turn.model`, append `turn.context` lines
  (privacy, clock and router work this way).
- `turn/text(text, turn)`: a synchronous pipe over every streamed text chunk (privacy restores here).
- `turn/end(turn)`: `turn.text` holds the final reply (memory captures preferences here).

## Services

| Service | Plugin | What it is |
| --- | --- | --- |
| `llm` | `llm` | `get(model_id)` returns a cached `ChatOpenAI` for the Nebius endpoint |
| `agent` | `agent` | `astream_events`, `astream`, `chat`, `stream` |
| `backend` | `workspace` | the Deep Agents filesystem backend |

## Run

```sh
python TG.py           # the bot, as before
python -m oche         # terminal chat with the same plugins
python test_oche.py    # tests (no network)
```
