#!/usr/bin/env python3
"""Tests for search-turn latency routing: fast paths, model mapping, Tavily depth.

Run:
    .venv/bin/python test_router_latency.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("NEBIUS_API_KEY", "test-key")

import router
from agent_service import AgentService
from router import (
    BASE_MODEL,
    INTENSE_MODEL,
    REASONING_MODEL,
    RouteDecision,
    choose_model,
    classify_request,
)
from tool import TavilySearchInput


def test_search_marker_fast_path_skips_classifier() -> None:
    # Prove no structured HTTP classifier call happens: patch it to explode.
    def _boom():  # noqa: ANN001
        raise AssertionError("classifier must not run on search fast path")

    original = router._classifier
    router._classifier = _boom
    try:
        for text in (
            "what's the latest news on the mars mission",
            "price of bitcoin right now",
            "search for the best python sql libraries",
            "who won the champions league final",
            "what's the weather forecast for this weekend",
            "when was the latest django release date announced",
        ):
            decision = classify_request(text)
            assert decision.intent == "current_information", (text, decision)
            assert decision.reason == "fast_path_search", (text, decision)
            assert decision.confidence >= 0.9, (text, decision)
    finally:
        router._classifier = original


def test_routine_queries_are_not_hijacked_into_search() -> None:
    # Personal/routine phrasings must stay on the routine fast path.
    for text in (
        "update on my project",
        "what did i say yesterday about cookies",
        "i am currently working on a refactor",
        "what time is it today",
        "remind me of our plans",
    ):
        decision = classify_request(text)
        assert decision.intent != "current_information", (text, decision)


def test_greeting_fast_path_still_wins() -> None:
    decision = classify_request("hi")
    assert decision.intent == "conversation"
    assert decision.reason == "fast_path_greeting"


def test_choose_model_search_turns_use_fast_tier() -> None:
    # Search turns answer from Tavily results: prefer the nano tier.
    model = choose_model(RouteDecision(intent="current_information", complexity="standard", confidence=0.9))
    assert model == BASE_MODEL, model

    model = choose_model(RouteDecision(intent="knowledge", complexity="standard", confidence=0.9))
    assert model == REASONING_MODEL, model

    model = choose_model(RouteDecision(intent="knowledge", complexity="routine", confidence=0.9))
    assert model == BASE_MODEL, model


def test_choose_model_serious_turns_keep_deep_tier() -> None:
    serious = RouteDecision(intent="knowledge", complexity="serious", confidence=0.95)
    assert choose_model(serious) == INTENSE_MODEL
    low_confidence = RouteDecision(intent="knowledge", complexity="routine", confidence=0.4)
    assert choose_model(low_confidence) == INTENSE_MODEL


def test_search_hint_only_for_current_information() -> None:
    decision = RouteDecision(intent="current_information", complexity="standard", confidence=0.95)
    prompt = AgentService._prompt_with_route("price of gold", "user1", decision)
    assert "ONE focused" in prompt and "tavily_search" in prompt

    routine = RouteDecision(intent="knowledge", complexity="routine", confidence=0.9)
    prompt = AgentService._prompt_with_route("tell me a joke", "user1", routine)
    assert "tavily_search" not in prompt


def test_tavily_depth_defaults_to_basic() -> None:
    schema = TavilySearchInput.model_fields["search_depth"]
    assert schema.default == "basic", schema.default


def test_agent_models_disable_thinking_except_serious_tier() -> None:
    base = AgentService._model(BASE_MODEL)
    assert base.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}

    reasoning = AgentService._model(REASONING_MODEL)
    assert reasoning.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}

    intense = AgentService._model(INTENSE_MODEL)
    assert not intense.extra_body, intense.extra_body


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"\033[92m✓ {test.__name__}\033[0m")
        except AssertionError as exc:
            failures += 1
            print(f"\033[91m✗ {test.__name__}: {exc}\033[0m")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"\033[91m✗ {test.__name__}: {type(exc).__name__}: {exc}\033[0m")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
