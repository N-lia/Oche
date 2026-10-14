"""Fast intent and entity routing for the OYI model cascade."""

from __future__ import annotations

import os
import logging
from functools import lru_cache
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
FAST_MODEL = os.getenv("OYI_FAST_MODEL", "nvidia/Nemotron-3-Nano-30B-A3B")
EVERYDAY_MODEL = os.getenv("OYI_EVERYDAY_MODEL", "nvidia/Nemotron-3-Super-120B-A12B")
REASONING_MODEL = os.getenv("OYI_REASONING_MODEL", "nvidia/Nemotron-3-Ultra")
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
        model=FAST_MODEL,
        base_url=NEBIUS_BASE_URL,
        api_key=os.environ["NEBIUS_API_KEY"],
        temperature=0,
    ).with_structured_output(RouteDecision)


def classify_request(user_input: str) -> RouteDecision:
    """Classify intent and extract useful entities with the fast model."""
    prompt = (
        "Classify this user request for model routing and extract only entities useful "
        "for answering it. Choose serious only for multi-step reasoning, difficult "
        "coding, high-stakes analysis, or explicit deep research. Choose routine for "
        "greetings, short answers, simple transformations, and straightforward lookups. "
        "Choose standard for normal explanations, coding help, planning, and web research.\n\n"
        f"User request:\n{user_input}"
    )
    try:
        return RouteDecision.model_validate(_classifier().invoke(prompt))
    except Exception:
        LOGGER.exception("Fast request classification failed; escalating to reasoning model")
        return RouteDecision(
            intent="analysis",
            complexity="serious",
            confidence=0,
            reason="classifier unavailable",
        )


def choose_model(decision: RouteDecision) -> str:
    """Map a classification to the model tier that should answer it."""
    if decision.confidence < 0.65 or decision.complexity == "serious":
        return REASONING_MODEL
    if decision.complexity == "standard":
        return EVERYDAY_MODEL
    return FAST_MODEL