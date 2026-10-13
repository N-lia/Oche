"""Turn Tavily payloads into focused, agent-friendly context."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

from .config import (
    PAGE_CONTENT_CHARS,
    RECOMMENDATION_LIMIT,
    RECOMMENDATION_SIGNAL_LIMIT,
    RESEARCH_CONTENT_CHARS,
    SEARCH_RESULT_LIMIT,
    SEARCH_SNIPPET_CHARS,
)

_WHITESPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?|\b\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(r"(?:[$€£]\s?\d[\d,.]*|\d[\d,.]*\s?(?:USD|EUR|GBP))", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_ACTION_RE = re.compile(
    r"\b(?:apply|book|buy|compare|contact|download|enroll|register|request|"
    r"reserve| sign up|shop|subscribe|try|visit|watch|read|learn|join)\b",
    re.IGNORECASE,
)
_DETAIL_RE = re.compile(
    r"\b(?:address|availability|benefits?|contact|cost|deadline|details?|"
    r"eligib|features?|location|opening hours|price|pricing|requirements?|"
    r"schedule|specifications?|terms|timeline| hours?)\b",
    re.IGNORECASE,
)
_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "how",
    "in",
    "is",
    "me",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "where",
    "with",
}


def _clean(value: object, limit: int) -> str:
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.") or "unknown source"


def _query_terms(query: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", query.lower())
        if len(term) > 2 and term not in _QUERY_STOPWORDS
    }


def _recommendation(title: str, content: str, url: str, query: str, score: object) -> str | None:
    text = f"{title} {content}".lower()
    signals: list[str] = []
    terms = _query_terms(query)
    matching_terms = sum(term in text for term in terms)
    if matching_terms:
        signals.append(f"matches {matching_terms} part{'s' if matching_terms != 1 else ''} of your request")
    if _DATE_RE.search(content):
        signals.append("contains date or time information")
    if _MONEY_RE.search(content):
        signals.append("contains cost or monetary information")
    if _DETAIL_RE.search(content):
        signals.append("includes concrete details")
    if _ACTION_RE.search(content):
        signals.append("offers a clear next action")
    if _NUMBER_RE.search(content) and "includes concrete details" not in signals:
        signals.append("contains specific facts")
    if isinstance(score, (int, float)) and score >= 0.75:
        signals.append("has a strong search relevance score")
    if signals:
        selected = signals[:RECOMMENDATION_SIGNAL_LIMIT]
        return f"Prioritize {title}: it {', '.join(selected)}."
    if url:
        return f"Consider {title} as a supporting source and verify its details."
    return None


def parse_tavily_search(data: dict, query: str = "") -> str:
    """Return ranked result summaries and practical next-step recommendations."""
    results = data.get("results") or []
    if not results:
        return f"No web results found for: {query!r}"

    seen_urls: set[str] = set()
    summaries: list[str] = []
    for item in results:
        url = str(item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        title = _clean(item.get("title") or url, 180)
        content = _clean(item.get("content"), SEARCH_SNIPPET_CHARS)
        score = item.get("score")
        score_text = f" | relevance {score:.2f}" if isinstance(score, (int, float)) else ""
        summaries.append(f"{len(summaries) + 1}. {title} [{_domain(url)}]{score_text}\n{content}\n{url}")
        if len(summaries) >= SEARCH_RESULT_LIMIT:
            break

    output = [f"Focused results for {query!r}:", "\n\n".join(summaries)]
    output.append("These are source leads, not verified conclusions. Synthesize only evidence relevant to the request.")
    return "\n\n".join(output)


def parse_tavily_pages(data: dict, per_page_chars: int = PAGE_CONTENT_CHARS) -> str:
    """Keep page extraction focused while retaining source URLs and failures."""
    sections: list[str] = []
    for item in data.get("results") or []:
        url = str(item.get("url") or "").strip()
        content = _clean(item.get("raw_content"), per_page_chars)
        if url and content:
            sections.append(f"Source: {url}\nRelevant content:\n{content}")
    sections.extend(
        f"Source failed: {item.get('url')}: {item.get('error') or 'unknown error'}"
        for item in data.get("failed_results") or []
    )
    return "\n\n".join(sections) or "No relevant content was returned."


def parse_tavily_research(data: dict) -> str:
    """Return a bounded research report with source links."""
    content = _clean(data.get("content"), RESEARCH_CONTENT_CHARS)
    sources: Iterable[dict] = data.get("sources") or []
    source_lines = [
        f"- {_clean(item.get('title') or item.get('url'), 160)}: {item.get('url')}"
        for item in sources
        if item.get("url")
    ]
    output = content or "Research completed but returned no content."
    if source_lines:
        output += "\n\nSources:\n" + "\n".join(source_lines)
    return output