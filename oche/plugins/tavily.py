"""Web search and reading through Tavily (search, extract, map, crawl, research)."""

from __future__ import annotations

from typing import Any

from oche.core import Context
from oche.langchain import from_langchain

name = "tavily"

PROMPT = """Web access (Tavily):
1. `tavily_search` is the default web tool. Use it for anything current: news, prices, releases, weather,
   scores, docs, or any question with "today", "latest", "recent", or a specific date. Write keyword-rich
   queries; use topic="news" or topic="finance" when it fits. If the snippets answer it, answer and cite.
2. `tavily_extract` only for URLs the user gave you, or 1-2 key pages whose snippets lacked the detail.
3. `tavily_map` only to list the pages on a site.
4. `tavily_crawl` only to read a whole docs section; keep `limit` at 3-10.
5. `tavily_research` only when the user explicitly asks for deep or comprehensive research (best in a subagent).
Do not search for pure reasoning, writing, maths, or basic coding. If a requested site gave nothing useful,
say so instead of padding with unrelated sources."""


def apply(ctx: Context, config: dict[str, Any]) -> None:
    from tool import TAVILY_TOOLS

    ctx.prompt(PROMPT)
    for lc_tool in TAVILY_TOOLS:
        ctx.tool(from_langchain(lc_tool, "Searching the web" if "search" in lc_tool.name else "Reading the web"))
