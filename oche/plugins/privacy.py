"""Scrub PII and secrets before anything leaves the machine; restore on the way back.

Wraps the repo's ``privacy.PrivacyVault``. Its ``turn/start`` hook runs first,
so the router and the model only ever see the scrubbed text.
"""

from __future__ import annotations

from typing import Any

from oche.core import Context

name = "privacy"


def apply(ctx: Context, config: dict[str, Any]) -> None:
    from privacy import scrub_for_external

    def scrub(turn: Any) -> None:
        turn.prompt, turn.state["vault"] = scrub_for_external(turn.prompt)

    def restore(text: str, turn: Any) -> str | None:
        vault = turn.state.get("vault")
        return vault.restore(text) if vault else None

    ctx.on("turn/start", scrub, prepend=True)
    ctx.on("turn/text", restore)
