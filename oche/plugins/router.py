"""Pick a model tier per turn with the existing fast router (``router.py``).

Sets ``turn.model`` and ``turn.state["route"]`` (a ``RouteDecision``), and adds
the same internal context lines the old service did.
"""

from __future__ import annotations

import asyncio
from typing import Any

from oche.core import Context

name = "router"


def apply(ctx: Context, config: dict[str, Any]) -> None:
    from router import choose_model, classify_request

    async def route(turn: Any) -> None:
        decision = await asyncio.to_thread(classify_request, turn.prompt)
        turn.state["route"] = decision
        turn.model = choose_model(decision)
        if decision.intent == "current_information":
            turn.context.append(
                "[Internal context: current-information turn. Answer after ONE focused "
                "tavily_search (max_results <= 5); requery only if the first results "
                "are clearly insufficient, then answer. Do not extract pages unless a "
                "result lacks the needed detail.]"
            )
        if decision.entities:
            entities = ", ".join(f"{key}={value}" for key, value in decision.entities.items())
            turn.context.append(f"[Internal context: extracted entities: {entities}]")

    ctx.on("turn/start", route)
