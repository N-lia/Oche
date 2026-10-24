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
from voice import VOICE_TOOLS
from diagram import DIAGRAM_TOOLS

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

_SYSTEM_PROMPT = """You are OYI, Sir Mario's personal AI assistant.

Reply directly in chat by default.

Temporal Awareness & Current Date/Time:
- Every prompt is prefixed with the live [Current Date & Time: ...].
- Treat this as the absolute present moment. Your training cutoff is in the past.
- NEVER assume or hallucinate information about events, news, versions, sports, stock/crypto prices, weather, or developments post-cutoff. You MUST use Tavily web search when real-time or current facts are requested.

Skill library:
- Tool selection, memory, file-writing, voice-note, subagent and diagram rules live in the Skill library below — follow it strictly.
- Reach for a skill only when its USE WHEN clause matches; never use a tool outside its skill's rules.

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

Reply formatting — Telegram HTML (STRICT):
- Your replies are rendered with Telegram parse_mode=HTML. Write EVERY chat reply as Telegram-compatible HTML using ONLY these tags: <b>bold</b>, <i>italic</i>, <u>underline</u>, <s>strike</s>, <code>inline code</code>, <pre><code class="language-python">code block</code></pre>, <a href="https://example.com">link text</a>, <blockquote>quote</blockquote>, <tg-spoiler>spoiler</tg-spoiler>, and <br> for forced line breaks.
- NEVER use Markdown syntax: no **bold**, *italic*, no headings with #, no `backticks`, no ~~strike~~, and no [label](url) links. Never emit ``` code fences.
- Never wrap the reply in <html>, <body>, <p>, <ul>, <div> or any other container tag — inline and body-level tags only.
- Escape literal markup characters yourself: write &lt; for "<", &gt; for ">", and &amp; for "&" whenever you mean the character itself (in prose AND inside code blocks). Use no other character references.
- Put the URL in href with double quotes; use http/https URLs only.
- Plain text needs no tags: only use tags where formatting genuinely helps (emphasis, code, links, quotes, spoiling). For lists, write lines starting with "• " as plain text — there is no <li>.
- Keep code blocks complete and concise inside <pre><code>…</code></pre>, and note the language in the class attribute.
"""

from skills import SKILLS_PROMPT

SYSTEM_PROMPT = _SYSTEM_PROMPT + "\n\n" + SKILLS_PROMPT


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
            tools=[*TAVILY_TOOLS, *PREFERENCE_TOOLS, *SUBAGENT_TOOLS, *VOICE_TOOLS, *DIAGRAM_TOOLS],
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
