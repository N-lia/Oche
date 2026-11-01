#!/usr/bin/env python3
"""Tests for the deterministic web-need policy.

Run:
    .venv/bin/python test_web_policy.py
"""

from __future__ import annotations

import sys

from web_policy import needs_web, search_query


def test_current_and_live_questions_need_web() -> None:
    for text in (
        "Who is the president of Nigeria?",
        "What is the latest Android version?",
        "What's the current price of Bitcoin?",
        "What's the best phone under 100k naira?",
        "What's the weather in Lagos?",
        "How much is an iPhone 17 in Nigeria?",
        "Who won the Champions League?",
        "latest news on the mars mission",
        "when was the latest django release date announced",
        "What is today's date and what is the weather in Lagos?",
    ):
        assert needs_web(text), text


def test_memory_and_stable_reasoning_stay_local() -> None:
    for text in (
        "What is my name and what response style do I prefer?",
        "What is my schedule today?",
        "what did i say yesterday about cookies",
        "i am currently working on a refactor",
        "remind me of our plans",
        "update on my project",
        "Remember that I prefer tldr",
        "write a python function to reverse a string",
        "What is 2+2?",
        "explain recursion",
        "how do I write a Python function?",
        "What is the capital of France?",
        "What's the current time?",
        "What time is it today",
        "best way to learn Python",
        "compare Python vs JavaScript",
        "Good morning",
    ):
        assert not needs_web(text), text


def test_explicit_search_request_always_wins() -> None:
    assert needs_web("search for the best python sql libraries")
    assert needs_web("google the flight status of BA 286")
    assert needs_web("look up this error message")


def test_search_query_removes_injected_context() -> None:
    raw = (
        "[Current Date & Time: Sunday, November 01, 2026 at 12:00:00 UTC]\n"
        "[Internal context: durable memory about this user. Known preferences: tldr]\n\n"
        "  What is the   price of bitcoin?  "
    )
    query = search_query(raw)
    assert query == "What is the price of bitcoin?", query


def test_search_query_is_bounded() -> None:
    query = search_query("word " * 500)
    assert len(query) <= 400, len(query)


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
