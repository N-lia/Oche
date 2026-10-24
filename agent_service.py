"""Model-backed agent service with routing, memory, privacy, and streaming."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import httpx

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends.filesystem import FilesystemBackend
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from memory import (
    PREFERENCE_TOOLS,
    add_memory,
    reset_current_user_id,
    search_memories,
    set_current_user_id,
)
from privacy import restore_for_local_delivery, scrub_for_external
from router import (
    BASE_MODEL,
    INTENSE_MODEL,
    NEBIUS_BASE_URL,
    RouteDecision,
    choose_model,
    classify_request,
)
from subagent import SUBAGENT_TOOLS
from tool import TAVILY_TOOLS
from device_tool import DEVICE_TOOLS
from voice import VOICE_TOOLS

load_dotenv(override=True)

WORKSPACE_DIR = os.environ.get(
    "WORKSPACE_DIR",
    "/data/oyi-memory/workspace" if os.path.isdir("/data/oyi-memory") else "./workspace",
)
os.makedirs(WORKSPACE_DIR, exist_ok=True)
_FS_BACKEND = FilesystemBackend(root_dir=WORKSPACE_DIR, virtual_mode=True)

# Register DeepAgents harness profile for OpenAI-compatible providers:
# 1. Disable the auto-added synchronous 'general-purpose' subagent and drop 'task' tool,
#    so the model exclusively uses our non-blocking 'spawn_subagent' tool.
# 2. Exclude dummy shell 'execute' tool.
register_harness_profile(
    "openai",
    HarnessProfile(
        excluded_tools=frozenset({"execute"}),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)

SYSTEM_PROMPT = """You are OYI, Sir Mario's personal AI assistant.

Reply directly in chat by default.

Temporal Awareness & Current Date/Time:
- Every prompt is prefixed with the live [Current Date & Time: ...].
- Treat this as the absolute present moment. Your training cutoff is in the past.
- NEVER assume or hallucinate information about events, news, versions, sports, stock/crypto prices, weather, or developments post-cutoff. You MUST use Tavily web search when real-time or current facts are requested.

Subagent Delegation Policy:
- You have an autonomous background tool: `spawn_subagent`.
- When Sir Mario requests a complex, deep, or long-running task (e.g. comprehensive multi-source web research, in-depth comparative reports, multi-file code architectures, or deep analysis) OR explicitly asks to do something in the background / with a subagent:
  1. Call `spawn_subagent` with a descriptive `task_name` and complete, detailed `instructions`.
  2. Acknowledge to Sir Mario that the subagent is working on it in the background and that he can freely continue chatting with you about other things.
- When Sir Mario asks quick, everyday questions, calculations, or routine tasks, answer directly yourself without delegating.

Web Access & Tavily Tool Selection Guide:
1. `tavily_search` (Web Discovery Engine - YOUR DEFAULT WEB TOOL):
   - When to use: When you need current information, facts, news, recent events, prices, software docs, or sources and NO specific URL is known.
   - MANDATORY TRIGGERS: Any question containing "today", "yesterday", "recently", "current", "latest", "new", "this week/month", specific dates, or live data (crypto/stocks, weather, sports scores, politics, tech releases).
   - Query Crafting: Write keyword-rich queries. Include the specific subject, year, or location. Set `topic="news"` for recent news articles, or `topic="finance"` for market data.
   - Snip-to-Answer Rule: If `tavily_search` returns clear, direct answers in its snippets, synthesize the answer immediately and cite the URLs. DO NOT call `tavily_extract` unless snippets lack the required detail.

2. `tavily_extract` (Deep Page Reader):
   - When to use: ONLY when the user explicitly provides one or more specific URLs (e.g. "Read this article: https://..."), OR when `tavily_search` found 1-2 critical pages whose snippets were too short to get the required code, table, or deep explanation.
   - When NOT to use: Do not call on random URLs. Do not call if snippets from `tavily_search` already gave the facts.

3. `tavily_map` (Site Structure Explorer):
   - When to use: When the user asks "What pages/docs are on this website?" or to discover URLs on a domain without reading page contents.
   - When NOT to use: Do not use for general questions or reading page text.

4. `tavily_crawl` (Multi-Page Site Reader):
   - When to use: When the user asks to ingest or read an entire section of documentation or a website (e.g., "Read the full tutorial series at https://...").
   - Caution: Keep `limit` small (3-10 pages) because crawling multiple pages is expensive and slow.

5. `tavily_research` (Long-Form Research Whitepaper):
   - When to use: ONLY when Sir Mario explicitly asks for "deep research", a "comprehensive research report", or an exhaustive investigation.
   - Best combined with `spawn_subagent` so the long research runs in the background.

When NOT to use Tavily at all:
- DO NOT use web search for pure reasoning, creative writing, mathematics, generic algorithms, or basic coding questions (e.g. "write a python function to reverse a string").
- DO NOT use web search for personal preferences, past discussions, or user background — use `recall_memory` instead.

Long-term memory & preferences:
- You have access to on-demand memory tools: `recall_memory`, `save_memory`, and `update_preference`.
- Call `recall_memory` ONLY when the user asks about prior conversations, past context, personal history, or project details you discussed previously. Do NOT call it for generic questions, coding, or everyday tasks.
- Call `save_memory` or `update_preference` when Sir Mario explicitly shares durable facts, background, or personal preferences (e.g. "remember that I...", "save this preference..."). Do NOT save temporary conversation chit-chat.

Phone control:
- You have `control_phone`, which drives Sir Mario's paired Android phone using a vision model and the phone's accessibility service.
- You have `phone_status`, which reports the app that is currently open on that phone and today's screen time without touching the screen.
- Use them ONLY when he explicitly asks to do something on the phone or asks about the phone's current screen/app. Do NOT use them for ordinary chat, research, or desktop tasks.
- For a phone request, pass a concrete natural-language instruction. Keep `max_steps` small for simple actions.
- Before destructive or irreversible phone actions (sending a message, deleting data, making a purchase), ask for confirmation in chat first. Never enter passwords, PINs, payment details, or bypass lock/security screens.
- Report the phone result honestly. If it timed out or was blocked, say so.

Response policy:
- Treat tool output as private working context. Never expose raw tool responses,
  search iterations, tool names, failed URL diagnostics, or internal reasoning.
- Synthesize the evidence into a direct answer to the user's request.
- Include only facts relevant to the request. If the sources do not support a
  claim, say that the information could not be verified.
- For recommendations, compare the relevant options and explain briefly why they
  fit. Do not recommend an item merely because a keyword appeared in its snippet.
- When a requested site has no usable results, say so clearly instead of filling
  the answer with results from unrelated sites.

File-writing policy:
- You may write, edit, or create files only when the user explicitly asks for a file,
  saved note, document, export, code file, script, report, or other persistent artifact.
- Do not create files for ordinary conversational replies, greetings, short answers,
  summaries, brainstorming, explanations, or casual chat.
- If the user asks you to "say", "tell me", "reply", "answer", "summarise", or "explain",
  respond in chat instead of writing a .txt, .md, or other file.
- Before using write_file or edit_file for an ambiguous request, ask one concise
  clarification question.
- When you do create or edit a file, mention the path briefly in your final reply.

Voice Notes & Spoken Audio Policy:
- You have access to `send_voice_note`.
- STRICT RULE: ONLY call `send_voice_note` when Sir Mario EXPLICITLY asks for a voice note, voice message, audio message, or spoken response (e.g. "send me a voice note", "reply in voice", "speak this to me").
- NEVER call `send_voice_note` for ordinary text chats, casual chit-chat, greetings, questions, explanations, code, or research. Ordinary conversation MUST ALWAYS be answered directly in chat text!

When Sir Mario explicitly requests a voice note, follow these guidelines:

Chatterbox-Turbo Paralinguistic Tags (Official Tokenizer Support):
- Physical Actions: [laugh], [chuckle], [gasp], [cough], [sigh], [groan], [sniff], [shush], [clear throat]
- Emotional States: [whispering], [angry], [fear], [surprised], [crying], [happy], [sarcastic], [dramatic]
- Content Types: [narration], [advertisement]
- DO NOT invent tags like [giggle], [roar], [cackle], [sob], [scream], or any other brackets. Only use the official tags listed above.

Tag Usage Best Practices:
1. Place emotion tags at the START of the sentence they affect (e.g., "[happy] Great job!" not "Great job! [happy]")
2. Use action tags INLINE where they naturally occur (e.g., "That's amazing [chuckle], really impressive work!")
3. DO NOT stack multiple emotion tags (e.g., "[happy][laugh]" is wrong; pick one)
4. Use tags sparingly (1-3 per voice note) to maintain natural flow
5. Match mood parameter to dominant emotion: mood="cheerful" for [happy]/[laugh]/[chuckle], mood="angry" for [angry]/[groan], mood="normal" for neutral or [sigh]

ONE-SHOT EXAMPLE (study this pattern):

User Request: "Send me a voice note congratulating me on finishing the deployment"
Correct Tool Call:
```
send_voice_note(
  text="[happy] Congratulations, Sir Mario! You absolutely crushed that deployment. [chuckle] Everything went live without a hitch. The logs are clean, the tests are passing, and your users are already seeing the new features. Well done!",
  mood="cheerful",
  event="task_done",
  caption="🎉 Deployment celebration"
)
```

WRONG Examples (DO NOT DO THIS):
❌ "[excited] Great work [laugh][chuckle]!" — 'excited' is not valid, tags are stacked
❌ "Amazing job! [happy]" — emotion tag should be at START, not end
❌ "[giggle] That's so cool!" — 'giggle' is not a valid tag, use [chuckle]
❌ "[dramatic pause] Then... [explosive laugh]" — these are not valid tags

After calling send_voice_note:
- Provide a brief chat acknowledgment like "Sent you a voice note above! 🎙️"
- Do NOT repeat the voice note text in your chat reply

Reply formatting — Telegram HTML (STRICT):
- Your replies are rendered with Telegram parse_mode=HTML. Write EVERY chat reply as Telegram-compatible HTML using ONLY these tags: <b>bold</b>, <i>italic</i>, <u>underline</u>, <s>strike</s>, <code>inline code</code>, <pre><code class="language-python">code block</code></pre>, <a href="https://example.com">link text</a>, <blockquote>quote</blockquote>, <tg-spoiler>spoiler</tg-spoiler>, and <br> for forced line breaks.
- NEVER use Markdown syntax: no **bold**, *italic*, no headings with #, no `backticks`, no ~~strike~~, and no [label](url) links. Never emit ``` code fences.
- Never wrap the reply in <html>, <body>, <p>, <ul>, <div> or any other container tag — inline and body-level tags only.
- Escape literal markup characters yourself: write &lt; for "<", &gt; for ">", and &amp; for "&" whenever you mean the character itself (in prose AND inside code blocks). Use no other character references.
- Put the URL in href with double quotes; use http/https URLs only.
- Plain text needs no tags: only use tags where formatting genuinely helps (emphasis, code, links, quotes, spoiling). For lists, write lines starting with "• " as plain text — there is no <li>.
- Keep code blocks complete and concise inside <pre><code>…</code></pre>, and note the language in the class attribute.
"""


class AgentService:
    """Own the model cascade and the agent's request lifecycle."""

    # Friendly labels for tool activity, shown in rich clients (WebApp).
    TOOL_LABELS = {
        "tavily_search": "Searching the web",
        "tavily_search_results_json": "Searching the web",
        "web_search": "Searching the web",
        "recall_memory": "Recalling memory",
        "save_memory": "Saving to memory",
        "update_preference": "Updating preferences",
        "spawn_subagent": "Spinning up a subagent",
        "control_phone": "Controlling your phone",
        "phone_status": "Checking your phone",
    }

    def __init__(self) -> None:
        self._checkpointer = MemorySaver()

    @staticmethod
    def _model(model_name: str) -> ChatOpenAI:
        # Thinking stays OFF for the latency-bound tiers: Nemotron's reasoning
        # pass otherwise burns seconds before the first visible token of every
        # round, including the tool-call round. Deep (serious) turns keep it.
        kwargs = (
            {}
            if model_name == INTENSE_MODEL
            else {
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            }
        )
        return ChatOpenAI(
            model=model_name,
            base_url=NEBIUS_BASE_URL,
            api_key=os.environ["NEBIUS_API_KEY"],
            streaming=True,
            # Bounded sockets: a stalled connection must not freeze a live
            # voice turn. The read timeout applies between streamed chunks.
            timeout=httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0),
            **kwargs,
        )

    @lru_cache(maxsize=3)
    def _agent_for_model(self, model_name: str):
        return create_deep_agent(
            model=self._model(model_name),
            tools=[*TAVILY_TOOLS, *PREFERENCE_TOOLS, *SUBAGENT_TOOLS, *VOICE_TOOLS, *DEVICE_TOOLS],
            system_prompt=SYSTEM_PROMPT,
            backend=_FS_BACKEND,
            checkpointer=self._checkpointer,
        )

    @staticmethod
    def _route(user_input: str) -> tuple[RouteDecision, str]:
        decision = classify_request(user_input)
        return decision, choose_model(decision)

    @staticmethod
    def get_config(thread_id: str = "me") -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}

    @classmethod
    def _prompt_with_route(cls, user_input: str, user_id: str, decision: RouteDecision) -> str:
        now_utc = datetime.now(timezone.utc)
        current_time_str = now_utc.strftime("%A, %B %d, %Y at %H:%M:%S UTC")

        context_lines = [f"[Current Date & Time: {current_time_str}]"]
        if decision.intent == "current_information":
            context_lines.append(
                "[Internal context: current-information turn. Answer after ONE focused "
                "tavily_search (max_results <= 5); requery only if the first results "
                "are clearly insufficient, then answer. Do not extract pages unless a "
                "result lacks the needed detail.]"
            )
        if decision.entities:
            entities = ", ".join(f"{key}={value}" for key, value in decision.entities.items())
            context_lines.append(f"[Internal context: extracted entities: {entities}]")

        header = "\n".join(context_lines)
        return f"{header}\n\n{user_input}"

    @staticmethod
    def _remember_exchange(user_input: str, response: str, user_id: str, intent: str = "") -> None:
        # Only embed and extract when the user explicitly communicated preference/biographical info
        if intent == "preference" and response.strip():
            add_memory(user_input, response, user_id)

    @staticmethod
    def assistant_text_chunk(chunk: Any) -> str:
        message_type = getattr(chunk, "type", "")
        if message_type not in {"AIMessage", "AIMessageChunk", "ai"}:
            return ""
        content = getattr(chunk, "content", "")
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )

    def chat(self, user_input: str, thread_id: str = "me") -> str:
        safe_input, vault = scrub_for_external(user_input)
        decision, model_name = self._route(safe_input)
        token = set_current_user_id(thread_id)
        try:
            response = self._agent_for_model(model_name).invoke(
                {"messages": [{"role": "user", "content": self._prompt_with_route(safe_input, thread_id, decision)}]},
                config=self.get_config(thread_id),
            )
        finally:
            reset_current_user_id(token)
        last_msg = response["messages"][-1]
        raw_answer = getattr(last_msg, "content", None)
        if raw_answer is None and isinstance(last_msg, dict):
            raw_answer = last_msg.get("content", "")
        if isinstance(raw_answer, list):
            raw_answer = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw_answer)
        answer = restore_for_local_delivery(str(raw_answer or ""), vault)
        self._remember_exchange(user_input, answer, thread_id, decision.intent)
        return answer

    def stream(self, user_input: str, thread_id: str = "me"):
        visible_text: list[str] = []
        safe_input, vault = scrub_for_external(user_input)
        decision, model_name = self._route(safe_input)
        token = set_current_user_id(thread_id)
        try:
            for chunk, _ in self._agent_for_model(model_name).stream(
                {"messages": [{"role": "user", "content": self._prompt_with_route(safe_input, thread_id, decision)}]},
                config=self.get_config(thread_id),
                stream_mode="messages",
            ):
                text = self.assistant_text_chunk(chunk)
                if text:
                    local_text = restore_for_local_delivery(text, vault)
                    visible_text.append(local_text)
                    yield local_text
        finally:
            reset_current_user_id(token)
        self._remember_exchange(user_input, "".join(visible_text), thread_id, decision.intent)

    async def astream(self, user_input: str, thread_id: str = "me"):
        visible_text: list[str] = []
        safe_input, vault = scrub_for_external(user_input)
        decision, model_name = await asyncio.to_thread(self._route, safe_input)
        token = set_current_user_id(thread_id)
        try:
            prompt = await asyncio.to_thread(self._prompt_with_route, safe_input, thread_id, decision)
            async for chunk, _ in self._agent_for_model(model_name).astream(
                {"messages": [{"role": "user", "content": prompt}]},
                config=self.get_config(thread_id),
                stream_mode="messages",
            ):
                text = self.assistant_text_chunk(chunk)
                if text:
                    local_text = restore_for_local_delivery(text, vault)
                    visible_text.append(local_text)
                    yield local_text
        finally:
            reset_current_user_id(token)
        await asyncio.to_thread(self._remember_exchange, user_input, "".join(visible_text), thread_id, decision.intent)

    async def astream_events(self, user_input: str, thread_id: str = "me"):
        """Structured stream for rich clients: status/tool/token events.

        Yields dicts: ``{"type": "status"|"tool"|"token"}`` and a final
        ``{"type": "done"}``. Tool detection is best-effort and never raises.
        """
        visible_text: list[str] = []
        safe_input, vault = scrub_for_external(user_input)
        decision, model_name = await asyncio.to_thread(self._route, safe_input)
        token = set_current_user_id(thread_id)
        announced: set[str] = set()
        try:
            prompt = await asyncio.to_thread(self._prompt_with_route, safe_input, thread_id, decision)
            yield {"type": "status", "phase": "thinking", "text": "Thinking…"}
            async for chunk, _meta in self._agent_for_model(model_name).astream(
                {"messages": [{"role": "user", "content": prompt}]},
                config=self.get_config(thread_id),
                stream_mode="messages",
            ):
                try:
                    if getattr(chunk, "type", "") == "tool":
                        name = getattr(chunk, "name", None) or "tool"
                        yield {
                            "type": "tool",
                            "name": name,
                            "label": self.TOOL_LABELS.get(name, name),
                            "state": "done",
                        }
                    else:
                        for call in getattr(chunk, "tool_calls", None) or []:
                            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
                            if name and name not in announced:
                                announced.add(name)
                                yield {
                                    "type": "tool",
                                    "name": name,
                                    "label": self.TOOL_LABELS.get(name, name),
                                    "state": "start",
                                }
                except Exception:  # noqa: BLE001 - activity is best-effort
                    pass
                text = self.assistant_text_chunk(chunk)
                if text:
                    local_text = restore_for_local_delivery(text, vault)
                    visible_text.append(local_text)
                    yield {"type": "token", "text": local_text}
        finally:
            reset_current_user_id(token)
        await asyncio.to_thread(self._remember_exchange, user_input, "".join(visible_text), thread_id, decision.intent)
        yield {"type": "done"}


agent_service = AgentService()
