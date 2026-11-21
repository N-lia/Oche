"""Build an App from a TOML config. The config is just an ordered list of plugins.

    [plugins.openai]            # -> oche.plugins.openai
    base_url = "https://..."
    [plugins."mypkg.myplugin"]  # anything else -> any importable module
    [plugins."openai:fast"]     # ":ident" loads the same plugin twice
    [plugins."~voice"]          # leading "~" disables a plugin

String values may use ${ENV_VAR}. Plugins load in file order.
"""

from __future__ import annotations

import importlib
import os
import tomllib
from pathlib import Path
from typing import Any

from oche.core import App

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "oche.toml"


def resolve(spec: str) -> Any:
    """``openai`` -> ``oche.plugins.openai``; anything else is imported as written."""
    module_name = spec.split(":", 1)[0]
    if "." not in module_name:
        try:
            return importlib.import_module(f"oche.plugins.{module_name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"oche.plugins.{module_name}":
                raise
    return importlib.import_module(module_name)


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = Path(path or os.environ.get("OCHE_CONFIG") or DEFAULT_CONFIG)
    with path.open("rb") as handle:
        return tomllib.load(handle)


def create_app(config: dict[str, Any] | None = None, *, only: set[str] | None = None, skip: set[str] = frozenset()) -> App:
    """Create an App and load every enabled plugin from ``config``."""
    config = load_config() if config is None else config
    app = App()
    for spec, plugin_config in (config.get("plugins") or {}).items():
        base = spec.split(":", 1)[0]
        if spec.startswith("~") or base in skip or (only is not None and base not in only):
            continue
        app.plugin(resolve(spec), _expand(plugin_config or {}))
    return app


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def prompt_file(name: str) -> str:
    """Text of ``oche/prompts/<name>.txt`` (each plugin's slice of the system prompt)."""
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
