#!/usr/bin/env python3
"""Tests for formatting Tavily payloads into model context.

Run:
    .venv/bin/python test_tavily_parser.py
"""

from __future__ import annotations

import sys

from tavily_response import parse_tavily_pages, parse_tavily_research, parse_tavily_search
from tavily_response import config


def test_search_includes_tavilys_synthesized_answer() -> None:
    data = {
        "answer": "Gold is trading near $2,400 per ounce.",
        "results": [
            {
                "title": "Gold price today",
                "url": "https://example.com/gold",
                "content": "Spot gold rose to $2,400/oz.",
                "score": 0.91,
            }
        ],
    }
    out = parse_tavily_search(data, query="gold price")
    assert "Tavily answer for 'gold price'" in out
    assert "Gold is trading near $2,400 per ounce." in out
    assert "1. Gold price today [example.com] | relevance 0.91" in out
    assert "https://example.com/gold" in out
    # Answer must come before the source list so the model reads it first.
    assert out.index("Tavily answer") < out.index("Focused results")


def test_search_without_results_but_with_answer() -> None:
    out = parse_tavily_search({"answer": "It is 31°C in Lagos."}, query="weather lagos")
    assert "It is 31°C in Lagos." in out
    assert "No source list was returned" in out


def test_search_without_results_or_answer() -> None:
    out = parse_tavily_search({"results": []}, query="nothing")
    assert out == "No web results found for: 'nothing'"


def test_search_deduplicates_and_caps_sources() -> None:
    results = [
        {
            "title": f"Result {index}",
            "url": f"https://example.com/{index % (config.SEARCH_RESULT_LIMIT + 5)}",
            "content": "body",
        }
        for index in range(config.SEARCH_RESULT_LIMIT + 10)
    ]
    out = parse_tavily_search({"results": results}, query="dedupe")
    urls = [line for line in out.splitlines() if line.startswith("https://example.com/")]
    assert len(urls) == len(set(urls))
    assert len(urls) <= config.SEARCH_RESULT_LIMIT
    assert "These are source leads, not verified conclusions." in out


def test_pages_include_content_and_failures() -> None:
    data = {
        "results": [{"url": "https://example.com/a", "raw_content": "Page body"}],
        "failed_results": [{"url": "https://example.com/b", "error": "403"}],
    }
    out = parse_tavily_pages(data)
    assert "Source: https://example.com/a" in out
    assert "Page body" in out
    assert "Source failed: https://example.com/b: 403" in out


def test_pages_empty_fallbacks() -> None:
    assert parse_tavily_pages({}) == "No relevant content was returned."


def test_research_includes_sources() -> None:
    data = {
        "content": "A long report.",
        "sources": [{"title": "Paper", "url": "https://example.com/paper"}],
    }
    out = parse_tavily_research(data)
    assert out.startswith("A long report.")
    assert "Sources:" in out
    assert "Paper: https://example.com/paper" in out


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
