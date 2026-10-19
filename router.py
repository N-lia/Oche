"""Fast intent and entity routing for the OYI model cascade."""

from __future__ import annotations

import os
import logging
from functools import lru_cache
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from privacy import PrivacyVault

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
ROUTER_MODEL = "nvidia/Nemotron-3_5-Lightning"
DAILY_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
BASE_MODEL = DAILY_MODEL  # alias for backward compatibility
REASONING_MODEL = "nvidia/nemotron-3-super-120b-a12b"
INTENSE_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"
LOGGER = logging.getLogger("oyi.router")


class RouteDecision(BaseModel):
    """Structured result from the fast classifier and entity extractor."""

    intent: Literal[
        "conversation",
        "knowledge",
        "current_information",
        "document",
        "coding",
        "planning",
        "analysis",
        "creative",
        "preference",
    ]
    complexity: Literal["routine", "standard", "serious"]
    entities: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="")


@lru_cache(maxsize=1)
def _classifier():
    return ChatOpenAI(
        model=ROUTER_MODEL,
        base_url=NEBIUS_BASE_URL,
        api_key=os.environ["NEBIUS_API_KEY"],
        temperature=0,
        max_tokens=1000,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False,
            }
        },
    ).with_structured_output(RouteDecision)


GREETING_WORDS = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "howdy", "sup", "yo", "oyi"}


COMPLEX_INDICATORS = {
    "research", "analyze", "analysis", "compare", "comparison",
    "deep", "subagent", "background", "benchmark", "architect",
    "refactor", "investigate", "audit", "comprehensive", "detailed",
    "write a script", "write code", "code project", "implement", "paper",
}


def classify_request(user_input: str) -> RouteDecision:
    """Classify intent and extract useful entities with the fast model."""
    cleaned = user_input.strip().lower()
    words = cleaned.split()

    # Fast-path 1: Greetings & casual openers (0ms)
    first_clean = cleaned.rstrip("!.?,")
    if first_clean in GREETING_WORDS or (len(words) <= 2 and any(w in GREETING_WORDS for w in words)):
        return RouteDecision(
            intent="conversation",
            complexity="routine",
            confidence=1.0,
            reason="fast_path_greeting",
        )

    # Fast-path 2: Short everyday queries without complex indicators (0ms)
    if len(words) <= 8 and not any(ind in cleaned for ind in COMPLEX_INDICATORS):
        return RouteDecision(
            intent="conversation",
            complexity="routine",
            confidence=0.9,
            reason="fast_path_routine",
        )

    user_input = PrivacyVault().scrub(user_input)
    prompt = (
        "You are an intent and complexity classifier. Immediately return the structured decision without explanation.\n"
        "Rules:\n"
        "- Choose 'serious' ONLY for complex multi-step reasoning, difficult code generation, or deep research.\n"
        "- Choose 'routine' for greetings, short questions, casual chat, simple edits, or straightforward lookups.\n"
        "- Choose 'standard' for normal questions, explanations, standard coding help, or web searches.\n\n"
        f"User request:\n{user_input}"
    )
    try:
        raw_result = _classifier().invoke(prompt)
        if isinstance(raw_result, RouteDecision):
            return raw_result
        return RouteDecision.model_validate(raw_result)
    except Exception as exc:
        LOGGER.warning("Fast request classification fallback to routine/base model: %s", exc)
        return RouteDecision(
            intent="conversation",
            complexity="routine",
            confidence=0.7,
            reason="classifier unavailable",
        )


def choose_model(decision: RouteDecision) -> str:
    """Map a classification to the model tier that should answer it."""
    if decision.confidence < 0.65 or decision.complexity == "serious":
        return INTENSE_MODEL
    if decision.complexity == "standard":
        return REASONING_MODEL
    return BASE_MODEL