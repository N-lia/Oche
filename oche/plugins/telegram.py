"""Telegram output rules, and delivery of ``notify`` events through the running bot.

The polling bot itself still lives in ``TG.py``; it registers its sender with the
legacy ``subagent`` module, and this plugin forwards ``notify`` events to it.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from oche.core import Context

name = "telegram"

LOGGER = logging.getLogger("oche.telegram")

PROMPT = """Reply formatting: Telegram HTML (strict).
- Use ONLY these tags: <b>, <i>, <u>, <s>, <code>, <pre><code class="language-x">…</code></pre>,
  <a href="https://…">text</a>, <blockquote>, <tg-spoiler>, and <br>.
- NEVER use Markdown (no **bold**, `backticks`, # headings, ``` fences, or [label](url) links).
- No container tags (<html>, <body>, <p>, <ul>, <div>). For lists, start lines with "• ".
- Escape literal characters as &lt; &gt; &amp; (also inside code). No other entities.
- Only format where it genuinely helps; plain text needs no tags."""


def apply(ctx: Context, config: dict[str, Any]) -> None:
    ctx.prompt(PROMPT, order=90)

    async def notify(target: str, title: str, text: str) -> None:
        legacy = sys.modules.get("subagent")
        sender = getattr(legacy, "_TELEGRAM_NOTIFIER", None)
        if sender is None:
            LOGGER.warning("no Telegram sender registered; dropping notification %r", title)
            return
        await sender(target, title, text)

    ctx.on("notify", notify)
