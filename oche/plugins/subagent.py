"""Background subagents: ``spawn_subagent`` hands off long work and returns immediately."""

from __future__ import annotations

from typing import Any

from oche.core import Context
from oche.loader import prompt_file

name = "subagent"


def apply(ctx: Context, config: dict[str, Any]) -> None:
    from subagent import SUBAGENT_TOOLS

    ctx.prompt(config.get("prompt") or prompt_file("subagent"), order=20)
    for tool in SUBAGENT_TOOLS:
        ctx.tool(tool, label="Spinning up a subagent")
