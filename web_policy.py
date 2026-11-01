"""Deterministic policy for when OYI must consult the live web.

Small models are unreliable at choosing a search tool on their own: a short
factual question looks like ordinary conversation to a token classifier, so it
gets answered from stale training data.  This module makes the decision in
plain Python before the model runs, which lets the agent pre-fetch evidence and
route the turn to a model that can synthesise it.

The policy is intentionally conservative: it favours a search for anything that
can change over time (prices, office holders, releases, weather, scores) and
avoids searching for stable reasoning, code, maths, or the user's own stored
preferences.
"""

from __future__ import annotations

import re

# Explicit requests to go to the web win over every exclusion below.
_EXPLICIT_RE = re.compile(
    r"\b(?:search|google|look\s+up|web\s+search|"
    r"browse\s+(?:the\s+)?(?:web|internet)|find\s+(?:it\s+)?online)\b",
    re.IGNORECASE,
)

# Anything whose answer can move with the calendar or the market.
_RECENCY_RE = re.compile(
    r"\b(?:today|tonight|yesterday|tomorrow|current(?:ly)?|latest|newest|"
    r"most\s+recent|recent(?:ly)?|right\s+now|at\s+the\s+moment|"
    r"this\s+(?:week|month|year|season)|breaking|live|news|headlines|"
    r"up[-\s]?to[-\s]?date|as\s+of\s+(?:now|today))\b",
    re.IGNORECASE,
)

_LIVE_DATA_RE = re.compile(
    r"\b(?:weather|forecast|temperature|rain(?:fall)?|humidity|"
    r"price|prices|pricing|cost|how\s+much|exchange\s+rate|"
    r"bitcoin|btc|ethereum|ether|crypto(?:currency)?|stock|stocks|shares|"
    r"market\s+cap|interest\s+rate|inflation|gdp|"
    r"score|scores|standings?|fixtures?|match(?:es)?|who\s+won|who\s+is\s+winning|"
    r"election|election\s+results?|release\s+date|released|out\s+yet|"
    r"version|launch\s+date|traffic|flight\s+status)\b",
    re.IGNORECASE,
)

# "Who is the president of X?" is a current-office question, not a stable fact.
_OFFICE_RE = re.compile(
    r"(?:\bwho\s+is\b|\bwho'?s\b|\bcurrent\b).{0,80}"
    r"\b(?:president|prime\s+minister|governor|minister|senator|ceo|"
    r"chancellor|head\s+of\s+state|pope|king|queen|monarch)\b|"
    r"\b(?:president|prime\s+minister|governor|ceo|minister)\s+of\b",
    re.IGNORECASE,
)

# Commercial recommendations depend on current models and prices.
_PRODUCT_RE = re.compile(
    r"\b(?:best|top|cheapest|affordable|recommended|which)\b.{0,80}"
    r"\b(?:phone|smartphone|laptop|tablet|camera|tv|television|car|vehicle|"
    r"gpu|cpu|monitor|headphones?|earbuds|smartwatch|router|printer|"
    r"vacuum|air\s+fryer|generator|solar|inverter|sim|data\s+plan|isp|"
    r"hotel|flight|insurance|loan|mortgage)\b",
    re.IGNORECASE,
)
_BUDGET_RE = re.compile(
    r"\b(?:under|below|less\s+than|around|within)\s*"
    r"(?:₦|n|ngn|\$|€|£|usd|ngn)?\s*\d",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b(?:2024|2025|2026|2027)\b")
_EVENT_WORD_RE = re.compile(
    r"\b(?:release|released|launch|launched|election|version|update|new|"
    r"changed|happened|announced|winner|won|sold|acquired|shutdown)\b",
    re.IGNORECASE,
)

# The user's own profile/preferences live in long-term memory, not the web.
_MEMORY_RE = re.compile(
    r"\b(?:my|me|mine|i|i'm|i\s+am)\b.{0,80}"
    r"\b(?:name|prefer|preference|favourite|favorite|remember|memory|"
    r"birthday|address|email|phone|project|codebase|goal|profile|"
    r"schedule|routine)\b|"
    r"\b(?:remember|recall|what\s+do\s+you\s+know\s+about\s+me)\b",
    re.IGNORECASE,
)
_PRIOR_RE = re.compile(
    r"\b(?:what\s+did\s+(?:i|we)\s+(?:say|tell|mention)|remind\s+me|"
    r"we\s+(?:discussed|talked)|our\s+(?:plans?|conversation|chat)|"
    r"last\s+time\s+we)\b",
    re.IGNORECASE,
)
_PERSONAL_STATEMENT_RE = re.compile(
    r"^\s*(?:i|i'm|i\s+am|we|we're|we\s+are)\s+"
    r"(?:am|'m|have|had|was|were|will|need|want|think|feel|like|prefer|"
    r"live|work|study|use|own|just|already|still|currently|working)",
    re.IGNORECASE,
)

_CODE_RE = re.compile(
    r"\b(?:write|debug|fix|refactor|implement|function|class|script|regex|"
    r"sql|python|javascript|typescript|java|kotlin|rust|golang|css|html|"
    r"algorithm|unit\s+test)\b",
    re.IGNORECASE,
)

_MATH_RE = re.compile(r"^\s*(?:what\s+is|calculate|compute|solve)?\s*[-+*/().\d\s=%]+\s*$", re.IGNORECASE)

# We already know the clock/date locally; a web search adds nothing.
_CLOCK_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)?\s+(?:the\s+)?(?:date|time|day)|what\s+time\s+is\s+it|"
    r"what\s+day\s+is\s+it|today'?s\s+date|current\s+time)\b",
    re.IGNORECASE,
)

_ADVICE_RE = re.compile(
    r"\b(?:how\s+(?:do|can|should)\s+(?:i|you|we)|how\s+to|explain|define|"
    r"what\s+does\s+.+\s+mean)\b",
    re.IGNORECASE,
)


def needs_web(text: str) -> bool:
    """Return True when the answer should be grounded in current web data."""
    value = (text or "").strip()
    if len(value) < 3:
        return False

    explicit = bool(_EXPLICIT_RE.search(value))
    if explicit:
        return True

    if _MATH_RE.match(value):
        return False

    recency = bool(_RECENCY_RE.search(value))
    live = bool(_LIVE_DATA_RE.search(value))
    office = bool(_OFFICE_RE.search(value))
    product = bool(_PRODUCT_RE.search(value) or _BUDGET_RE.search(value))
    dated_event = bool(_YEAR_RE.search(value) and _EVENT_WORD_RE.search(value))

    # We already know the clock/date locally.  A combined question such as
    # "today's date and the weather" still needs the web for its live part.
    if _CLOCK_RE.search(value) and not (live or office or product or dated_event):
        return False

    # Personal questions and prior-conversation references are answered from
    # memory.  A live-data word still wins ("what is my current internet plan
    # price?" needs both memory and current pricing), so only exclude when no
    # live/office/product signal exists.  Generic recency ("my schedule today")
    # stays in memory.
    personal = bool(
        _MEMORY_RE.search(value)
        or _PRIOR_RE.search(value)
        or (_PERSONAL_STATEMENT_RE.match(value) and not value.rstrip().endswith("?"))
    )
    if personal and not (live or office or product or dated_event):
        return False

    if recency or live or office or product or dated_event:
        return True

    # Stable knowledge, code, and advice stay local.
    if _CODE_RE.search(value) or _ADVICE_RE.search(value):
        return False

    return False


def search_query(text: str) -> str:
    """Turn a user turn into a compact Tavily query."""
    value = re.sub(r"\[Current Date & Time:[^\]]*\]", "", text or "")
    value = re.sub(r"\[Internal context:.*?\]", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:400]
