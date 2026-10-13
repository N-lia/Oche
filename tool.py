"""Tavily web access tools for the OYI agent.

This module exposes Tavily's five documented capabilities as LangChain tools,
following Tavily's agent guidance at https://docs.tavily.com/agents.md.

    tavily_search    Discover current sources when relevant URLs are unknown.
    tavily_extract   Read known URLs as clean, model-ready content.
    tavily_map       Discover a site's URLs without reading page content.
    tavily_crawl     Read multiple pages from a single site.
    tavily_research  Produce a long-form, cited research report.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Literal

import httpx
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field
from tavily import AsyncTavilyClient
from tavily import errors as tavily_errors

from tavily_response import parse_tavily_pages, parse_tavily_research, parse_tavily_search

LOGGER = logging.getLogger("oyi.tavily")

# Tavily API limits documented at:
# https://docs.tavily.com/documentation/api-reference
_MAX_RESULTS = 20
_MAX_URLS_PER_CALL = 20
_MAX_DEPTH = 5

# Internal limits keep interactive requests predictable and cost-aware.
_MAX_MAP_LIMIT = 100
_CRAWL_DEFAULT_PAGES = 10
_CRAWL_MAX_PAGES = 50
_RESEARCH_DEFAULT_TIMEOUT = 300
_RESEARCH_POLL_SECONDS = 5

# Output budgets keep tool responses within the model context window.
_EXTRACT_CHARS_PER_URL = 12_000
_CRAWL_CHARS_PER_PAGE = 4_000
_RESEARCH_CHARS = 30_000

_ExtractDepth = Literal["basic", "advanced"]

_CLIENT: AsyncTavilyClient | None = None


class TavilySearchInput(BaseModel):
    """Input schema optimized for reliable agent tool selection."""

    query: str = Field(
        ...,
        description=(
            "Natural-language web search query. Include the specific entity, date, "
            "jurisdiction, product name, or source type needed for the user's question."
        ),
        min_length=1,
    )
    search_depth: Literal["advanced", "basic", "fast", "ultra-fast"] = Field(
        "advanced",
        description=(
            "Use 'advanced' by default for reliable relevance. Use 'basic' for simple "
            "lookups where broad accuracy is enough. Use 'fast' or 'ultra-fast' only "
            "when latency matters more than recall."
        ),
    )
    topic: Literal["general", "news", "finance"] = Field(
        "general",
        description=(
            "Use 'general' for normal web search, 'news' for recent articles or events, "
            "and 'finance' for market/company/financial news."
        ),
    )
    max_results: int = Field(
        5,
        ge=1,
        le=_MAX_RESULTS,
        description=(
            "Number of ranked results to return. Use 3-5 for normal answers; increase "
            "toward 10-20 only for comparison, source discovery, or uncertain topics."
        ),
    )
    time_range: Literal["day", "week", "month", "year"] | None = Field(
        None,
        description=(
            "Optional relative recency filter. Use this for current or recent topics. "
            "Do not combine with start_date/end_date."
        ),
    )
    start_date: str | None = Field(
        None,
        description="Optional absolute start date as YYYY-MM-DD. Pair with end_date.",
    )
    end_date: str | None = Field(
        None,
        description="Optional absolute end date as YYYY-MM-DD. Pair with start_date.",
    )
    include_domains: list[str] | None = Field(
        None,
        description=(
            "Only return results from these domains, such as ['anthropic.com'] or "
            "['docs.tavily.com']. Use when the user asks for official or specific sources."
        ),
    )
    exclude_domains: list[str] | None = Field(
        None,
        description=(
            "Exclude these domains from results. Use for low-quality, duplicate, or "
            "user-disallowed sources."
        ),
    )


class TavilyExtractInput(BaseModel):
    urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=_MAX_URLS_PER_CALL,
        description=(
            "Absolute http(s) URLs to read. Use URLs found by tavily_search or supplied "
            "by the user. Do not pass vague site names or search queries here."
        ),
    )
    query: str | None = Field(
        None,
        description=(
            "Optional question or intent used to return the most relevant chunks from "
            "long pages. Use this when extracting documentation, articles, or reports "
            "for a specific user question."
        ),
    )
    extract_depth: _ExtractDepth = Field(
        "basic",
        description=(
            "Use 'basic' for normal pages. Use 'advanced' for pages likely to need "
            "better parsing, tables, embedded content, or JavaScript-rendered content."
        ),
    )


class TavilyMapInput(BaseModel):
    url: str = Field(
        ...,
        description=(
            "Root URL of the site or section to inspect, for example "
            "'https://docs.tavily.com'."
        ),
    )
    instructions: str | None = Field(
        None,
        description=(
            "Optional natural-language filter for which URLs to discover. Use only when "
            "regex path filters are insufficient, because it increases cost."
        ),
    )
    max_depth: int = Field(
        1,
        ge=1,
        le=_MAX_DEPTH,
        description="Link depth from the root URL. Keep at 1 unless deeper site discovery is needed.",
    )
    limit: int = Field(
        50,
        ge=1,
        le=_MAX_MAP_LIMIT,
        description="Maximum URLs to return. Keep small unless broad site discovery is required.",
    )
    select_paths: list[str] | None = Field(
        None,
        description="Regex path patterns URLs must match, such as ['/docs/.*'].",
    )
    exclude_paths: list[str] | None = Field(
        None,
        description="Regex path patterns URLs must not match, such as ['/blog/.*'].",
    )


class TavilyCrawlInput(BaseModel):
    url: str = Field(..., description="Root URL of the site or section to crawl.")
    instructions: str | None = Field(
        None,
        description=(
            "Optional natural-language guidance for selecting pages. Use sparingly; "
            "prefer select_paths/exclude_paths when the URL pattern is clear."
        ),
    )
    max_depth: int = Field(
        1,
        ge=1,
        le=_MAX_DEPTH,
        description="Link depth from the root URL. Use the shallowest depth that can answer the task.",
    )
    limit: int = Field(
        _CRAWL_DEFAULT_PAGES,
        ge=1,
        le=_CRAWL_MAX_PAGES,
        description=(
            "Maximum pages to read. Keep near the default for interactive chat; crawling "
            "many pages is slower and uses more credits."
        ),
    )
    extract_depth: _ExtractDepth = Field(
        "basic",
        description="Use 'advanced' only for complex pages, tables, or JavaScript-rendered content.",
    )
    select_paths: list[str] | None = Field(
        None,
        description="Regex path patterns page URLs must match, such as ['/docs/.*'].",
    )
    exclude_paths: list[str] | None = Field(
        None,
        description="Regex path patterns page URLs must not match.",
    )


class TavilyResearchInput(BaseModel):
    input: str | None = Field(
        None,
        description=(
            "The deep research question or task. Provide only when starting a new "
            "research job; omit when resuming with request_id."
        ),
    )
    model: Literal["mini", "pro", "auto"] = Field(
        "auto",
        description=(
            "Use 'mini' for narrow reports, 'pro' for complex multi-angle research, "
            "and 'auto' when unsure."
        ),
    )
    request_id: str | None = Field(
        None,
        description=(
            "Existing research task id to resume or poll. Use this instead of starting "
            "a duplicate task after a timeout."
        ),
    )
    timeout_seconds: int = Field(
        _RESEARCH_DEFAULT_TIMEOUT,
        ge=30,
        le=600,
        description="How long to wait for the report before returning a resumable request_id.",
    )


class _ToolFailure(Exception):
    """Operational failure with a message suitable for agent-facing output."""


def _get_client() -> AsyncTavilyClient:
    """Return the shared async Tavily client."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = AsyncTavilyClient()
    return _CLIENT


def _recovery_hint(exc: Exception) -> str | None:
    """Convert expected Tavily and network failures into actionable guidance.

    Returns None for unexpected exceptions so they propagate and get logged by
    the caller instead of being converted into tool output.
    """
    if isinstance(exc, _ToolFailure):
        return str(exc)

    # Check specific Tavily exceptions before their broader base classes.
    if isinstance(exc, tavily_errors.TavilyKeylessLimitError):
        retry = exc.retry_after_seconds
        wait = f" Retry in {retry}s." if retry else ""
        return (
            "Tavily's keyless rate limit was hit. "
            f"Set TAVILY_API_KEY (free at app.tavily.com) for higher limits.{wait}"
        )
    if isinstance(exc, tavily_errors.UsageLimitExceededError):
        return (
            "Tavily rate limit hit (HTTP 429). Do not retry immediately; "
            "wait before trying again and check usage at app.tavily.com."
        )
    if isinstance(exc, tavily_errors.ForbiddenError):
        detail = str(exc).strip()
        suffix = f" ({detail})" if detail else ""
        return (
            "Tavily plan or usage limit exceeded"
            f"{suffix}. Tell the user to review the plan at app.tavily.com/billing."
        )
    if isinstance(exc, tavily_errors.InvalidAPIKeyError):
        return "Tavily rejected the API key. Check TAVILY_API_KEY in .env."
    if isinstance(exc, tavily_errors.MissingAPIKeyError):
        return "TAVILY_API_KEY is not set. Create a key at app.tavily.com and add it to .env."
    if isinstance(exc, tavily_errors.KeylessUnsupportedEndpointError):
        return f"{exc} Set TAVILY_API_KEY (free at app.tavily.com) and retry."
    if isinstance(exc, tavily_errors.BadRequestError):
        return f"Tavily rejected the request as invalid: {exc}. Fix the arguments and retry once."
    if isinstance(exc, tavily_errors.TimeoutError):
        return f"{exc} Retry, or reduce scope (fewer URLs, pages, or results)."
    if isinstance(exc, httpx.HTTPError):
        return "Network error reaching api.tavily.com. Retry later."
    return None


async def _respond(
    call: Callable[[], Awaitable[dict]],
    format_result: Callable[[dict], str],
) -> str:
    """Execute a Tavily operation and format the response."""
    try:
        data = await call()
    except Exception as exc:
        hint = _recovery_hint(exc)
        if hint is None:
            raise
        LOGGER.warning("Tavily call failed (%s): %s", type(exc).__name__, exc)
        return f"Tavily error: {hint}"
    return format_result(data)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[truncated {len(text) - limit} characters]"


def _format_search(data: dict) -> str:
    return parse_tavily_search(data, query=str(data.get("query") or ""))


def _format_pages(data: dict, per_page_chars: int) -> str:
    return parse_tavily_pages(data, per_page_chars=per_page_chars)


def _format_map(data: dict) -> str:
    urls = data.get("results") or []
    if not urls:
        return "No URLs discovered."
    return f"{len(urls)} URLs:\n" + "\n".join(str(url) for url in urls)


def _format_research(data: dict) -> str:
    return parse_tavily_research(data)


async def _run_research(
    client: AsyncTavilyClient,
    input: str | None,
    request_id: str | None,
    model: str,
    timeout_seconds: int,
) -> dict:
    """Create (or resume) a research task and poll until it completes."""
    if request_id is None:
        if not (input or "").strip():
            raise _ToolFailure(
                "Provide `input` (the research question), or `request_id` to resume a previous task."
            )
        task = await client.research(input=input.strip(), model=model)
        request_id = task.get("request_id")
        if not request_id:
            raise _ToolFailure(f"Tavily did not return a request_id for the research task: {task!r}")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        result = await client.get_research(request_id)
        status = result.get("status")
        if status == "completed":
            return result
        if status == "failed":
            raise _ToolFailure(
                f"Research task {request_id} failed. Retry with a narrower `input` or model='mini'."
            )
        if loop.time() >= deadline:
            raise _ToolFailure(
                f"Research task {request_id} is still running after {timeout_seconds}s "
                f"(status: {status!r}). Do not start a new task; call tavily_research "
                f"again with request_id='{request_id}' to resume."
            )
        await asyncio.sleep(_RESEARCH_POLL_SECONDS)


@tool(args_schema=TavilySearchInput)
async def tavily_search(
    query: str,
    search_depth: Literal["advanced", "basic", "fast", "ultra-fast"] = "advanced",
    topic: Literal["general", "news", "finance"] = "general",
    max_results: int = 5,
    time_range: Literal["day", "week", "month", "year"] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> str:
    """Search the live web and return ranked results with scores and content snippets.

    Use this when the user needs current information, source discovery, recent
    news, official documentation, market/company information, or facts that may
    have changed. Use it as the first step when you do not know which URLs are
    relevant. After search, call tavily_extract on the best source(s) before
    relying on details, quotes, or nuanced claims in your answer.

    Do not use this for purely conversational replies, stable facts already in
    context, math/code reasoning that does not need the web, or when the user
    already provided the exact URL to read. This returns snippets and URLs, not
    full page content.

    Args:
        query: Natural-language search query.
        search_depth: "advanced" for best relevance (2 credits), "basic" for
            balanced (1 credit), "fast" for lower latency (1 credit), or
            "ultra-fast" for the lowest latency with one summary per result.
        topic: "general" for web results, "news" for articles, "finance" for
            market and company news.
        max_results: How many results to return, 1-20 (default 5).
        time_range: Relative recency filter: "day", "week", "month", or "year".
            Use this OR start_date/end_date, not both.
        start_date: Absolute start date as YYYY-MM-DD (pair with end_date).
        end_date: Absolute end date as YYYY-MM-DD (pair with start_date).
        include_domains: Only return results from these domains, e.g. ["nature.com"].
        exclude_domains: Drop results from these domains, e.g. ["reddit.com"].
    """
    if time_range is not None:
        # Tavily does not accept relative and absolute date filters together.
        start_date = end_date = None
    client = _get_client()
    return await _respond(
        lambda: client.search(
            query=query,
            search_depth=search_depth,
            topic=topic,
            max_results=max(1, min(max_results, _MAX_RESULTS)),
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        ),
        _format_search,
    )


@tool(args_schema=TavilyExtractInput)
async def tavily_extract(
    urls: list[str],
    query: str | None = None,
    extract_depth: _ExtractDepth = "basic",
) -> str:
    """Read one or more URLs (1-20) and return their clean content without HTML.

    Use this after tavily_search to ground an answer in the most relevant source,
    or when the user provides a specific URL. Use `query` for long pages so the
    result focuses on chunks relevant to the user's question. This is the right
    tool before quoting, summarizing, or citing a known page.

    Do not use this to discover unknown sources; use tavily_search first. Do
    not use it for many pages from the same site; use tavily_map or tavily_crawl
    when site-wide discovery is needed. Costs 1 credit per 5 URLs at basic
    depth, 2 per 5 at advanced; failed URLs are never charged.

    Args:
        urls: Absolute http(s) URLs to read, 1-20 per call.
        query: Optional intent (e.g. the user's question); returns only the most
            relevant ~800-character chunks instead of the full page.
        extract_depth: "basic" (default), or "advanced" for tables, JS-rendered
            pages, and embedded content (higher success, more latency).
    """
    client = _get_client()
    return await _respond(
        lambda: client.extract(
            urls=urls[:_MAX_URLS_PER_CALL],
            extract_depth=extract_depth,
            query=query,
        ),
        partial(_format_pages, per_page_chars=_EXTRACT_CHARS_PER_URL),
    )


@tool(args_schema=TavilyMapInput)
async def tavily_map(
    url: str,
    instructions: str | None = None,
    max_depth: int = 1,
    limit: int = 50,
    select_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> str:
    """Discover a website's URLs without reading any page content.

    Use this to understand a site's structure, find relevant documentation
    pages, or choose URLs for later tavily_extract calls. It is the lightweight
    preflight before tavily_crawl because it returns URL lists only and does not
    read page content.

    Do not use this when you need the content of a page, a direct answer, or web
    results across many domains. Use tavily_search for cross-site discovery and
    tavily_extract or tavily_crawl for content. Costs 1 credit per 10 discovered
    pages, or 2 per 10 when `instructions` is set.

    Args:
        url: Root URL to start from, e.g. "https://docs.example.com".
        instructions: Optional natural-language filter, e.g. "pages about the
            Python SDK". Use when regex patterns are not enough; this doubles cost.
        max_depth: Link hops from the root, 1-5 (default 1; higher is slower).
        limit: Maximum number of URLs to return (default 50).
        select_paths: Regex patterns URLs must match, e.g. ["/docs/.*"].
        exclude_paths: Regex patterns URLs must not match, e.g. ["/changelog/.*"].
    """
    client = _get_client()
    return await _respond(
        lambda: client.map(
            url=url,
            instructions=instructions,
            max_depth=max(1, min(max_depth, _MAX_DEPTH)),
            limit=max(1, min(limit, _MAX_MAP_LIMIT)),
            select_paths=select_paths,
            exclude_paths=exclude_paths,
        ),
        _format_map,
    )


@tool(args_schema=TavilyCrawlInput)
async def tavily_crawl(
    url: str,
    instructions: str | None = None,
    max_depth: int = 1,
    limit: int = _CRAWL_DEFAULT_PAGES,
    extract_depth: _ExtractDepth = "basic",
    select_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> str:
    """Read many pages of one website (content included), starting from a root URL.

    Use this when several pages from the same site must be read, such as
    collecting a docs section or auditing a small site area. Prefer tavily_map
    first to discover URL patterns, then crawl with tight select_paths and a
    small limit.

    Do not use this for a single known URL; use tavily_extract. Do not use it
    for broad web discovery across domains; use tavily_search. Avoid large
    crawls in normal chat because every page costs mapping plus extraction
    credits and output is truncated per page.

    Args:
        url: Root URL to crawl.
        instructions: Optional natural-language guidance for which pages or
            content to include (adds mapping cost).
        max_depth: Link hops from the root, 1-5 (default 1).
        limit: Maximum pages to crawl. Keep this small; default 10, max 50.
        extract_depth: "basic" (default), or "advanced" for tables and JS pages.
        select_paths: Regex patterns page URLs must match, e.g. ["/docs/.*"].
        exclude_paths: Regex patterns page URLs must not match.
    """
    client = _get_client()
    return await _respond(
        lambda: client.crawl(
            url=url,
            instructions=instructions,
            max_depth=max(1, min(max_depth, _MAX_DEPTH)),
            limit=max(1, min(limit, _CRAWL_MAX_PAGES)),
            extract_depth=extract_depth,
            select_paths=select_paths,
            exclude_paths=exclude_paths,
        ),
        partial(_format_pages, per_page_chars=_CRAWL_CHARS_PER_PAGE),
    )


@tool(args_schema=TavilyResearchInput)
async def tavily_research(
    input: str | None = None,
    model: Literal["mini", "pro", "auto"] = "auto",
    request_id: str | None = None,
    timeout_seconds: int = _RESEARCH_DEFAULT_TIMEOUT,
) -> str:
    """Run Tavily's deep-research agent: a long-form report with cited sources.

    Use this only when the user explicitly asks for deep research, a long-form
    cited report, a decision-ready comparison, or a multi-question investigation
    that needs autonomous source gathering. It is slow and expensive: 4-110
    credits for "mini", 15-250 for "pro".

    Do not use this for ordinary Q&A, quick fact checks, recent news lookups, or
    tasks answerable with tavily_search plus tavily_extract. If it times out,
    resume with request_id; do not start a duplicate research job.

    Args:
        input: The research question or task. Omit when resuming via request_id.
        model: "mini" for narrow questions (cheaper, faster), "pro" for complex
            multi-angle research, "auto" (default) to let Tavily decide.
        request_id: Resume or poll an existing research task instead of creating one.
        timeout_seconds: How long to wait for completion, 30-600 (default 300).
    """
    client = _get_client()
    return await _respond(
        lambda: _run_research(
            client,
            input=input,
            request_id=request_id,
            model=model,
            timeout_seconds=max(30, min(timeout_seconds, 600)),
        ),
        _format_research,
    )


TAVILY_TOOLS: list[BaseTool] = [
    tavily_search,
    tavily_extract,
    tavily_map,
    tavily_crawl,
    tavily_research,
]
