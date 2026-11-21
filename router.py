"""Fast intent and entity routing for the OYI model cascade."""

from __future__ import annotations

import os
import logging
import re
from functools import lru_cache
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from privacy import PrivacyVault
from web_policy import needs_web

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
        "device_control",
    ]
    complexity: Literal["routine", "standard", "serious"]
    entities: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="")
    # Set by the deterministic web policy, not by the model.  A web turn is
    # pre-fetched and routed to a model that can synthesise sources reliably.
    needs_web: bool = False


@lru_cache(maxsize=1)
def _classifier():
    return ChatOpenAI(
        model=ROUTER_MODEL,
        base_url=NEBIUS_BASE_URL,
        api_key=os.environ["NEBIUS_API_KEY"],
        temperature=0,
        max_tokens=1000,
        # The classifier once stalled ~90s on a structured-output call, which
        # froze the whole turn before the agent even started. Bound it tight
        # and fall back to the routine route (the exception path below).
        timeout=8.0,
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


# Wording that means "do something on the paired Android phone". A bare "my
# phone" is not enough: "how much is my phone worth?" and "what's the weather
# on my phone?" are normal questions, while "open Settings on my phone" must
# reach the control_phone tool.
_PHONE_DEVICE_RE = re.compile(
    r"\b(?:my\s+phone|the\s+phone|my\s+device|the\s+device|my\s+screen|"
    r"phone\s+screen|phone'?s\s+screen|on\s+the\s+phone)\b|\bcontrol\s+(?:my|the)\s+phone\b",
    re.IGNORECASE,
)
_PHONE_ACTION_RE = re.compile(
    r"\b(?:open|launch|tap|click|press|type|enter|swipe|scroll|navigate|"
    r"control|install|uninstall|download|update|send|message|text|call|dial|play|"
    r"pause|turn\s+(?:on|off)|enable|disable|set|change|check|find|search|"
    r"look\s+at|read|show|take\s+a\s+(?:screenshot|photo)|record|toggle|"
    r"reply|delete|unlock|do|use|what(?:'s|\s+is)\s+on|what\s+app|"
    r"which\s+app|screen\s+time|phone_status)\b",
    re.IGNORECASE,
)


_PHONE_DIRECT_RE = re.compile(
    r"\b(?:(?:open|launch)\s+(?:the\s+)?(?:\w+\s+app|settings|whatsapp|youtube|"
    r"chrome|spotify|instagram|tiktok|gmail|camera|photos|gallery|calculator|"
    r"calendar|clock|contacts|messages|play\s+store|maps)|"
    r"(?:turn\s+(?:on|off)|enable|disable|toggle)\s+(?:the\s+)?(?:dark\s+(?:mode|theme)|"
    r"wi-?fi|bluetooth|hotspot|airplane\s+mode|do\s+not\s+disturb|flashlight|"
    r"torch|auto-?rotate|battery\s+saver|location)|"
    r"(?:what|which)\s+app\s+(?:is\s+open|am\s+i\s+(?:using|in|on))|"
    r"how\s+much\s+(?:screen\s+)?time\s+have\s+i\s+used(?:\s+today)?|"
    r"my\s+screen\s+time|screen\s+time\s+today)\b|"
    r"^\s*(?:scroll\s+(?:up|down|left|right)|go\s+back|go\s+home|open\s+recents)\b",
    re.IGNORECASE,
)


def _is_phone_control(text: str) -> bool:
    # "How do I ... on my phone?" asks for instructions, not for the agent to
    # start tapping.  Explicit screen inspection ("what's on my screen") stays.
    if re.match(
        r"^\s*(?:how\s+(?:do|can|should)\s+i|how\s+to|"
        r"what(?:'s|\s+is)\s+the\s+best\s+way)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    if _PHONE_DIRECT_RE.search(text):
        return True
    return bool(_PHONE_DEVICE_RE.search(text) and _PHONE_ACTION_RE.search(text))


def classify_request(user_input: str) -> RouteDecision:
    """Classify intent and extract useful entities with the fast model."""
    cleaned = user_input.strip().lower()
    words = cleaned.split()
    web = needs_web(cleaned)

    # Fast-path 1: Greetings & casual openers (0ms)
    first_clean = cleaned.rstrip("!.?,")
    if first_clean in GREETING_WORDS or (len(words) <= 2 and any(w in GREETING_WORDS for w in words)):
        return RouteDecision(
            intent="conversation",
            complexity="routine",
            confidence=1.0,
            reason="fast_path_greeting",
        )

    # Fast-path 2: phone-control requests (0ms).
    # A device word alone is not enough; otherwise "how much is my phone
    # worth?" or "what's the weather on my phone?" would be hijacked.  An
    # action verb plus a device reference is a reliable signal, and it must
    # beat the web policy for "open the weather app on my phone".
    if _is_phone_control(cleaned):
        return RouteDecision(
            intent="device_control",
            complexity="routine",
            confidence=0.95,
            reason="fast_path_phone_control",
        )

    # Fast-path 3: OBVIOUS search / current-information turns (0ms).
    # The deterministic policy catches factual and current questions that a
    # token classifier would otherwise label as ordinary conversation.
    if web:
        return RouteDecision(
            intent="current_information",
            complexity="standard",
            confidence=0.95,
            reason="fast_path_web_policy",
            needs_web=True,
        )

    # Fast-path 4: Short everyday queries without complex indicators (0ms)
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
            result = raw_result
        else:
            result = RouteDecision.model_validate(raw_result)
        # The model's label can be wrong for short factual turns; the
        # deterministic policy is the source of truth for web grounding.
        result.needs_web = web
        if web and result.intent not in {"current_information", "device_control"}:
            result.intent = "current_information"
            if result.complexity == "routine":
                result.complexity = "standard"
        return result
    except Exception as exc:
        LOGGER.warning("Fast request classification fallback to routine/base model: %s", exc)
        if web:
            return RouteDecision(
                intent="current_information",
                complexity="standard",
                confidence=0.7,
                reason="classifier unavailable; web policy requires search",
                needs_web=True,
            )
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
    # Phone control must actually call the tool. The smaller daily model tends to
    # describe the phone instead of acting on it, which reads as a success that
    # never happened, so these turns go to a model with reliable tool use.
    if decision.intent == "device_control":
        return REASONING_MODEL
    # Web turns need synthesis, citation discipline, and instruction following.
    # Tavily already supplied the sources; the 120B model turns them into a
    # coherent answer instead of stopping mid-thought like the nano tier.
    if decision.needs_web or decision.intent == "current_information":
        return REASONING_MODEL
    if decision.complexity == "standard":
        return REASONING_MODEL
    return BASE_MODEL