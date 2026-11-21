"""The agent's file workspace: a Deep Agents filesystem backend, as the ``backend`` service."""

from __future__ import annotations

import os
from typing import Any

from oche.core import Context
from oche.loader import prompt_file

name = "workspace"


def default_root() -> str:
    return os.environ.get(
        "WORKSPACE_DIR", "/data/oyi-memory/workspace" if os.path.isdir("/data/oyi-memory") else "./workspace"
    )


def apply(ctx: Context, config: dict[str, Any]) -> None:
    from deepagents.backends.filesystem import FilesystemBackend

    root = config.get("root") or default_root()
    os.makedirs(root, exist_ok=True)
    ctx.provide("backend", FilesystemBackend(root_dir=root, virtual_mode=True))
    ctx.prompt(config.get("prompt") or prompt_file("workspace"))
