"""Scrub PII and secrets before anything leaves the machine; restore on the way back.

Wraps the repo's ``privacy.PrivacyVault``. Load it before plugins that read the
prompt (router, memory) so they only ever see the scrubbed text.
"""

from __future__ import annotations

from typing import Any

from oche.core import Context

name = "privacy"


def apply(ctx: Context, config: dict[str, Any]) -> None:
    from privacy import PrivacyVault

    def scrub(turn: Any) -> None:
        vault = PrivacyVault()
        turn.prompt = vault.scrub(turn.prompt)
        turn.state["vault"] = vault

    def restore(text: str, turn: Any) -> str | None:
        vault = turn.state.get("vault")
        return vault.restore(text) if vault else None

    ctx.on("turn/start", scrub, prepend=True)
    ctx.on("turn/text", restore)
