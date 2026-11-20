"""Oche core: a Cordis-style plugin context in one small file.

Everything is a plugin. A plugin is a callable ``apply(ctx, config)`` (or a
module/object with an ``apply`` attribute) plus optional ``name`` and
``inject`` attributes. Whatever a plugin registers through its ``ctx``
(event listeners, services, tools, prompt sections, background tasks, child
plugins) is tracked by its scope and undone when the scope is disposed.

``inject`` lists services the plugin needs. A plugin whose services are not
all present waits in a pending state, activates as soon as they appear, and
is deactivated again (back to pending) if one of them goes away.

The core knows nothing about models, chat, Telegram or memory. Those are
plugins too, including the agent loop itself (``oche.plugins.agent``).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("oche")

Disposer = Callable[[], Any]


# --------------------------------------------------------------------------- tools


@dataclass
class Tool:
    """A model-callable function. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., Any]
    label: str = ""  # friendly activity label for rich clients

    async def __call__(self, **kwargs: Any) -> str:
        result = self.run(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, str) else str(result)

    def schema(self) -> dict[str, Any]:
        """OpenAI-style function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def schema_from_signature(fn: Callable[..., Any]) -> dict[str, Any]:
    """Infer a minimal JSON Schema from simple type hints (str/int/float/bool/list/dict)."""
    hints = {}
    try:
        hints = inspect.get_annotations(fn, eval_str=True)
    except Exception:  # noqa: BLE001 - fall back to strings
        pass
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        hint = hints.get(name, str)
        origin = getattr(hint, "__origin__", hint)
        properties[name] = {"type": _JSON_TYPES.get(origin, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            properties[name]["default"] = param.default
    return {"type": "object", "properties": properties, "required": required}


# --------------------------------------------------------------------------- scope


class Scope:
    """One plugin instance: its config, its registrations, and its children."""

    def __init__(self, root: _Root, parent: Scope | None, plugin: Any, config: Any) -> None:
        self.root = root
        self.parent = parent
        self.plugin = plugin
        self.config = config
        self.name = getattr(plugin, "name", None) or getattr(plugin, "__name__", "plugin")
        self.inject: tuple[str, ...] = tuple(getattr(plugin, "inject", ()) or ())
        self.children: list[Scope] = []
        self.disposables: list[Disposer] = []
        self.active = False
        self.disposed = False
        self.ctx = Context(root, self)

    def __repr__(self) -> str:
        state = "disposed" if self.disposed else "active" if self.active else "pending"
        return f"<Scope {self.name} {state}>"

    # Activation ---------------------------------------------------------------

    def ready_to_start(self) -> bool:
        return all(name in self.root.services for name in self.inject)

    def start(self) -> None:
        if self.active or self.disposed or not self.ready_to_start():
            return
        self.active = True
        apply = getattr(self.plugin, "apply", self.plugin)
        try:
            apply(self.ctx, self.config)
        except Exception:
            LOGGER.exception("plugin %s failed to apply", self.name)
            self.stop()
            raise

    def stop(self) -> None:
        """Undo everything this scope registered; it may start again later."""
        if not self.active:
            return
        self.active = False
        for child in list(reversed(self.children)):
            child.dispose()
        self.children.clear()
        while self.disposables:
            disposer = self.disposables.pop()
            try:
                result = disposer()
                if inspect.isawaitable(result):
                    self.root.spawn(result)
            except Exception:  # noqa: BLE001 - keep tearing down
                LOGGER.exception("disposer failed in plugin %s", self.name)

    def dispose(self) -> None:
        """Stop for good and detach from the parent."""
        if self.disposed:
            return
        self.disposed = True  # first, so a refresh during teardown cannot restart us
        self.stop()
        if self.parent and self in self.parent.children:
            self.parent.children.remove(self)
        self.root.scopes.pop(self, None)


# --------------------------------------------------------------------------- root


class _Root:
    """Shared state behind every Context of one app."""

    def __init__(self) -> None:
        self.services: dict[str, Any] = {}
        self.listeners: dict[str, list[Callable[..., Any]]] = {}
        self.tools: dict[str, Tool] = {}
        self.prompts: list[tuple[int, Any]] = []
        self.scopes: dict[Scope, None] = {}  # ordered set, load order
        self.tasks: set[asyncio.Task[Any]] = set()
        self.ready = False

    def spawn(self, awaitable: Awaitable[Any]) -> asyncio.Task[Any] | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            LOGGER.warning("no running event loop; dropping background work")
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            return None
        task = loop.create_task(awaitable)  # type: ignore[arg-type]
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            LOGGER.error("background task failed", exc_info=task.exception())

    def refresh(self) -> None:
        """Start pending scopes whose services are now present; stop broken ones."""
        changed = True
        while changed:
            changed = False
            for scope in list(self.scopes):
                if scope.disposed:
                    continue
                if scope.active and not scope.ready_to_start():
                    scope.stop()
                    changed = True
                elif not scope.active and scope.ready_to_start() and (scope.parent is None or scope.parent.active):
                    try:
                        scope.start()
                    except Exception:  # noqa: BLE001 - logged in start()
                        continue
                    changed = True


# --------------------------------------------------------------------------- context


class Context:
    """The handle a plugin uses to register things. Cheap: a (root, scope) pair."""

    def __init__(self, root: _Root, scope: Scope | None) -> None:
        self._root = root
        self.scope = scope

    # Plugins ------------------------------------------------------------------

    def plugin(self, plugin: Any, config: Any = None) -> Scope:
        """Load a child plugin. Returns its scope; ``scope.dispose()`` unloads it."""
        scope = Scope(self._root, self.scope, plugin, {} if config is None else config)
        if self.scope is not None:
            self.scope.children.append(scope)
        self._root.scopes[scope] = None
        if self.scope is None or self.scope.active:
            scope.start()
        if not scope.active:
            LOGGER.info("plugin %s pending on %s", scope.name, [s for s in scope.inject if s not in self._root.services])
        return scope

    def effect(self, disposer: Disposer) -> Disposer:
        """Register cleanup to run when this plugin is unloaded."""
        if self.scope is not None:
            self.scope.disposables.append(disposer)
        return disposer

    @property
    def config(self) -> Any:
        return self.scope.config if self.scope else {}

    # Services -----------------------------------------------------------------

    def provide(self, name: str, value: Any) -> None:
        """Expose ``value`` as service ``name`` for as long as this plugin lives."""
        if name in self._root.services:
            raise ValueError(f"service {name!r} is already provided")
        self._root.services[name] = value

        def remove() -> None:
            if self._root.services.get(name) is value:
                del self._root.services[name]
                self._root.refresh()

        self.effect(remove)
        self._root.refresh()

    def get(self, name: str, default: Any = None) -> Any:
        return self._root.services.get(name, default)

    def __getitem__(self, name: str) -> Any:
        return self._root.services[name]

    def __getattr__(self, name: str) -> Any:
        # Services read like attributes: ``ctx.agent``, ``ctx.model``.
        services = self.__dict__.get("_root").services if "_root" in self.__dict__ else {}
        if name in services:
            return services[name]
        raise AttributeError(name)

    # Events -------------------------------------------------------------------

    def on(self, name: str, handler: Callable[..., Any], *, prepend: bool = False) -> Disposer:
        handlers = self._root.listeners.setdefault(name, [])
        handlers.insert(0, handler) if prepend else handlers.append(handler)
        if name == "ready" and self._root.ready:
            self._root.spawn(_call(handler))

        def off() -> None:
            if handler in handlers:
                handlers.remove(handler)

        return self.effect(off)

    async def emit(self, name: str, *args: Any) -> None:
        """Call every handler; a failing handler is logged, not raised."""
        for handler in list(self._root.listeners.get(name, ())):
            try:
                await _call(handler, *args)
            except Exception:  # noqa: BLE001
                LOGGER.exception("handler for %r failed", name)

    async def bail(self, name: str, *args: Any) -> Any:
        """Return the first handler result that is not ``None``."""
        for handler in list(self._root.listeners.get(name, ())):
            result = await _call(handler, *args)
            if result is not None:
                return result
        return None

    async def pipe(self, name: str, value: Any, *args: Any) -> Any:
        """Thread ``value`` through each handler; ``None`` keeps it unchanged."""
        for handler in list(self._root.listeners.get(name, ())):
            result = await _call(handler, value, *args)
            if result is not None:
                value = result
        return value

    # Tools and prompt ---------------------------------------------------------

    def tool(self, fn_or_tool: Any = None, /, **options: Any) -> Any:
        """Register a tool. Use as ``ctx.tool(Tool(...))``, ``@ctx.tool`` or ``@ctx.tool(label=...)``."""
        if fn_or_tool is None:
            return lambda fn: self.tool(fn, **options)
        tool = fn_or_tool if isinstance(fn_or_tool, Tool) else Tool(
            name=options.get("name") or fn_or_tool.__name__,
            description=options.get("description") or inspect.cleandoc(fn_or_tool.__doc__ or ""),
            parameters=options.get("parameters") or schema_from_signature(fn_or_tool),
            run=fn_or_tool,
            label=options.get("label", ""),
        )
        tools = self._root.tools
        if tool.name in tools:
            LOGGER.warning("tool %s is being replaced", tool.name)
        tools[tool.name] = tool

        def remove() -> None:
            if tools.get(tool.name) is tool:
                del tools[tool.name]

        self.effect(remove)
        return fn_or_tool

    @property
    def tools(self) -> dict[str, Tool]:
        return dict(self._root.tools)

    def prompt(self, section: str | Callable[..., Any], *, order: int = 50) -> Disposer:
        """Contribute a system-prompt section (text, or ``fn(turn) -> str | None``)."""
        entry = (order, section)
        self._root.prompts.append(entry)
        self._root.prompts.sort(key=lambda item: item[0])

        def remove() -> None:
            if entry in self._root.prompts:
                self._root.prompts.remove(entry)

        return self.effect(remove)

    async def system_prompt(self, turn: Any = None) -> str:
        parts: list[str] = []
        for _, section in list(self._root.prompts):
            text = await _call(section, turn) if callable(section) else section
            if text:
                parts.append(inspect.cleandoc(text))
        return "\n\n".join(parts)

    # Background work ----------------------------------------------------------

    def task(self, awaitable: Awaitable[Any]) -> asyncio.Task[Any] | None:
        """Run ``awaitable`` in the background; it is cancelled when this plugin unloads."""
        task = self._root.spawn(awaitable)
        if task is not None:
            self.effect(lambda: task.cancel() if not task.done() else None)
        return task


async def _call(fn: Callable[..., Any], *args: Any) -> Any:
    result = fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


# --------------------------------------------------------------------------- app


class App(Context):
    """The root context. ``await app.start()`` fires ``ready``; ``await app.stop()`` unloads all."""

    def __init__(self) -> None:
        root = _Root()
        super().__init__(root, Scope(root, None, _app_plugin, {}))
        self.scope.active = True
        root.scopes[self.scope] = None

    @property
    def is_ready(self) -> bool:
        return self._root.ready

    async def start(self) -> App:
        if not self._root.ready:
            self._root.ready = True
            await self.emit("ready")
        return self

    async def stop(self) -> None:
        await self.emit("dispose")
        self.scope.stop()
        tasks = [t for t in self._root.tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._root.ready = False


def _app_plugin(ctx: Context, config: Any) -> None:  # the root "plugin"
    return None


_app_plugin.name = "app"  # type: ignore[attr-defined]
