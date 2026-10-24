---
name: tavily
purpose: Find current information, news, prices, and sources on the live web with Tavily.
---

## Use when

- the user needs current information, facts, news, recent events, prices, software docs, or sources and NO specific URL is known
- the question contains "today", "yesterday", "recently", "current", "latest", "new", "this week/month", specific dates, or live data (crypto/stocks, weather, sports scores, politics, tech releases)
- the user explicitly asks to search the web, google something, look something up, or find sources

## Do not use when

- pure reasoning, creative writing, mathematics, generic algorithms, or basic coding questions (e.g. "write a python function to reverse a string")
- personal preferences, past discussions, or user background — use `recall_memory` instead
- the user already provided the exact URL to read (use `tavily_extract` directly)

## How

Tool selection, in priority order:

1. `tavily_search` (Web Discovery Engine — YOUR DEFAULT WEB TOOL): use when you need current information and no specific URL is known. Write keyword-rich queries including the subject, year, or location. Set `topic="news"` for recent news or `topic="finance"` for market data. MANDATORY for any "today/recently/latest/current" turn.
2. `tavily_extract` (Deep Page Reader): use ONLY when the user provides specific URLs, or when `tavily_search` found 1-2 critical pages whose snippets were too short for the needed code, table, or deep explanation. Never call it on random URLs.
3. `tavily_map` (Site Structure Explorer): use when the user asks "What pages/docs are on this website?" or to discover URLs on a domain without reading page contents. Not for general questions or page text.
4. `tavily_crawl` (Multi-Page Site Reader): use when the user asks to ingest or read an entire section of documentation or a website. Keep `limit` small (3-10 pages) because crawling is slow and expensive.
5. `tavily_research` (Long-Form Research Whitepaper): use ONLY when the user explicitly asks for "deep research", a "comprehensive research report", or an exhaustive investigation. Best combined with `spawn_subagent` so the long research runs in the background.

Snip-to-Answer Rule: if `tavily_search` returns clear, direct answers in its snippets, synthesize the answer immediately and cite the URLs. Do NOT call `tavily_extract` unless snippets lack the required detail. Treat tool output as private working context: never expose raw tool responses, search iterations, tool names, failed URL diagnostics, or internal reasoning — synthesize the evidence into a direct answer.

## Example

User: "What's the latest on the Artemis program?"
Correct: one focused `tavily_search` with `topic="news"`, then answer from the snippets with source links.
Wrong: `tavily_research` for an everyday news question, or `tavily_crawl` of nasa.gov.
