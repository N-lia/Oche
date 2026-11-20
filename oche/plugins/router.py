"""Pick a model tier per turn: free heuristics first, a tiny classifier last.

Sets ``turn.model`` and ``turn.state["route"]`` (intent, complexity,
confidence). Config keys: ``routine``, ``standard``, ``serious``, ``search``
(model ids), ``classifier`` (model id, empty to disable), ``timeout``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from oche.core import Context

name = "router"
inject = ["model"]

LOGGER = logging.getLogger("oche.router")

GREETINGS = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "howdy", "sup", "yo", "oyi"}
COMPLEX = (
    "research", "analyze", "analysis", "compare", "comparison", "deep", "subagent", "background",
    "benchmark", "architect", "refactor", "investigate", "audit", "comprehensive", "detailed",
    "write a script", "write code", "code project", "implement", "paper",
)
SEARCH = (
    "search", "google", "look up", "lookup", "find out", "latest", "newest", "news", "breaking",
    "price", "prices", "stock", "crypto", "weather", "forecast", "exchange rate",
    "who is winning", "who won", "what happened", "was released", "release date",
)
INTENTS = (
    "conversation", "knowledge", "current_information", "document", "coding",
    "planning", "analysis", "creative", "preference",
)
CLASSIFY = """Classify the user request. Reply with JSON only:
{"intent": one of %s, "complexity": "routine"|"standard"|"serious", "confidence": 0..1}
"serious" only for complex multi-step reasoning, difficult code, or deep research.
"routine" for greetings, short questions, chit-chat, simple edits or lookups.

Request:
""" % ", ".join(INTENTS)


def heuristic(text: str) -> dict[str, Any] | None:
    cleaned = text.strip().lower()
    words = cleaned.split()
    if cleaned.rstrip("!.?,") in GREETINGS or (len(words) <= 2 and any(w in GREETINGS for w in words)):
        return {"intent": "conversation", "complexity": "routine", "confidence": 1.0, "reason": "greeting"}
    if any(marker in cleaned for marker in SEARCH):
        return {"intent": "current_information", "complexity": "standard", "confidence": 0.95, "reason": "search"}
    if len(words) <= 8 and not any(marker in cleaned for marker in COMPLEX):
        return {"intent": "conversation", "complexity": "routine", "confidence": 0.9, "reason": "short"}
    return None


def parse_decision(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    data = json.loads(match.group(0) if match else raw)
    intent = data.get("intent") if data.get("intent") in INTENTS else "conversation"
    complexity = data.get("complexity") if data.get("complexity") in ("routine", "standard", "serious") else "standard"
    confidence = min(1.0, max(0.0, float(data.get("confidence", 0.7))))
    return {"intent": intent, "complexity": complexity, "confidence": confidence, "reason": "classifier"}


def choose(decision: dict[str, Any], tiers: dict[str, str]) -> str:
    if decision["confidence"] < 0.65 or decision["complexity"] == "serious":
        return tiers["serious"]
    if decision["intent"] == "current_information":
        return tiers["search"]
    return tiers[decision["complexity"]]


def apply(ctx: Context, config: dict[str, Any]) -> None:
    routine = config.get("routine", "")
    tiers = {
        "routine": routine,
        "standard": config.get("standard", routine),
        "serious": config.get("serious", config.get("standard", routine)),
        "search": config.get("search", routine),
    }
    classifier = config.get("classifier", "")
    timeout = float(config.get("timeout", 8.0))

    async def classify(text: str) -> dict[str, Any]:
        decision = heuristic(text)
        if decision or not classifier:
            return decision or {"intent": "conversation", "complexity": "standard", "confidence": 0.8, "reason": "default"}
        try:
            raw = await asyncio.wait_for(
                ctx.model.complete(
                    [{"role": "user", "content": CLASSIFY + text}],
                    model=classifier,
                    temperature=0,
                    max_tokens=200,
                ),
                timeout,
            )
            return parse_decision(raw)
        except Exception as exc:  # noqa: BLE001 - routing must never block a turn
            LOGGER.warning("classifier fallback: %s", exc)
            return {"intent": "conversation", "complexity": "routine", "confidence": 0.7, "reason": "fallback"}

    async def route(turn: Any) -> None:
        if turn.model:  # caller pinned a model (e.g. a subagent)
            return
        decision = await classify(turn.prompt)
        turn.state["route"] = decision
        turn.model = choose(decision, tiers) or None
        if decision["intent"] == "current_information":
            turn.context.append(
                "[Internal context: current-information turn. Answer after ONE focused web search "
                "(max_results <= 5); search again only if the first results are clearly insufficient.]"
            )

    ctx.on("turn/start", route)
