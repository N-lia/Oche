"""Web search and reading through Tavily (search, extract, map, crawl, research)."""

from __future__ import annotations

from typing import Any

from oche.core import Context
from oche.loader import prompt_file

name = "tavily"


def apply(ctx: Context, config: dict[str, Any]) -> None:
    from tool import TAVILY_TOOLS

    ctx.prompt(config.get("prompt") or prompt_file("tavily"))
    for tool in TAVILY_TOOLS:
        ctx.tool(tool, label="Searching the web" if "search" in tool.name else "Reading the web")
