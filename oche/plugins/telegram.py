"""Telegram output rules: replies are written as Telegram HTML."""

from __future__ import annotations

from typing import Any

from oche.core import Context
from oche.loader import prompt_file

name = "telegram"


def apply(ctx: Context, config: dict[str, Any]) -> None:
    ctx.prompt(config.get("prompt") or prompt_file("telegram"), order=90)
