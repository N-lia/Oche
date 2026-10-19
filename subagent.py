"""Background subagent execution and proactive delivery for OYI."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from datetime import datetime, timezone

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from memory import _CURRENT_USER_ID, PREFERENCE_TOOLS
from router import NEBIUS_BASE_URL, REASONING_MODEL
from tool import TAVILY_TOOLS

WORKSPACE_DIR = os.environ.get(
    "WORKSPACE_DIR",
    "/data/oyi-memory/workspace" if os.path.isdir("/data/oyi-memory") else "./workspace",
)
os.makedirs(WORKSPACE_DIR, exist_ok=True)
_FS_BACKEND = FilesystemBackend(root_dir=WORKSPACE_DIR, virtual_mode=True)

LOGGER = logging.getLogger("oyi.subagent")

TelegramNotifier = Callable[[int | str, str, str], Awaitable[None]]
_TELEGRAM_NOTIFIER: TelegramNotifier | None = None
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def register_telegram_notifier(notifier: TelegramNotifier) -> None:
    """Register the proactive Telegram delivery callback from TG.py."""
    global _TELEGRAM_NOTIFIER, _MAIN_LOOP
    _TELEGRAM_NOTIFIER = notifier
    try:
        _MAIN_LOOP = asyncio.get_running_loop()
    except RuntimeError:
        pass


class SpawnSubagentInput(BaseModel):
    task_name: str = Field(..., min_length=2, description="Short, clear name of the background task.")
    instructions: str = Field(..., min_length=5, description="Comprehensive, detailed instructions for the subagent to complete.")


async def _run_subagent_task(chat_id: str, task_name: str, instructions: str) -> None:
    """Execute a background subagent task using the reasoning model and tools."""
    LOGGER.info("Starting background subagent task_name='%s' chat_id=%s", task_name, chat_id)
    thread_id = f"subagent-{uuid.uuid4().hex[:8]}"
    now_utc = datetime.now(timezone.utc)
    current_time_str = now_utc.strftime("%A, %B %d, %Y at %H:%M:%S UTC")

    subagent_prompt = f"""You are an expert autonomous subagent working for Sir Mario.
Current Date & Time: {current_time_str}
Your mission: {task_name}

Instructions from primary agent:
{instructions}

Guidelines:
- Carry out deep research or work thoroughly using available tools.
- When finished, summarize your findings into a comprehensive, well-structured, professional report.
- Do not mention that you are a subagent; address Sir Mario directly with the completed deliverable.
"""

    model = ChatOpenAI(
        model=REASONING_MODEL,
        base_url=NEBIUS_BASE_URL,
        api_key=os.environ["NEBIUS_API_KEY"],
        temperature=0.2,
    )

    try:
        subagent = create_deep_agent(
            model=model,
            tools=[*TAVILY_TOOLS, *PREFERENCE_TOOLS],
            system_prompt=subagent_prompt,
            backend=_FS_BACKEND,
        )

        response = await subagent.ainvoke(
            {"messages": [{"role": "user", "content": f"Please execute the task: {task_name}\n\n{instructions}"}]},
            config={"configurable": {"thread_id": thread_id}},
        )

        last_msg = response["messages"][-1]
        final_content = getattr(last_msg, "content", None)
        if final_content is None and isinstance(last_msg, dict):
            final_content = last_msg.get("content", "")
        if isinstance(final_content, list):
            final_content = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in final_content
            )
        elif not isinstance(final_content, str):
            final_content = str(final_content or "")

        LOGGER.info("Subagent finished task_name='%s' chat_id=%s", task_name, chat_id)

        if _TELEGRAM_NOTIFIER:
            await _TELEGRAM_NOTIFIER(chat_id, task_name, final_content)
        else:
            LOGGER.warning("No Telegram notifier registered to deliver subagent result for %s", task_name)

    except Exception:
        LOGGER.exception("Background subagent failed task_name='%s' chat_id=%s", task_name, chat_id)
        if _TELEGRAM_NOTIFIER:
            await _TELEGRAM_NOTIFIER(
                chat_id,
                task_name,
                f"⚠️ The background task '{task_name}' encountered an error during execution. Please try asking again.",
            )


@tool(args_schema=SpawnSubagentInput)
async def spawn_subagent(task_name: str, instructions: str) -> str:
    """Launch an autonomous background subagent for long-running, deep research, coding, or complex tasks.
    Returns immediately so the user can continue chatting. When completed, the subagent delivers its final
    report directly to the user in Telegram."""
    chat_id = _CURRENT_USER_ID.get()
    if not chat_id:
        return "Cannot launch subagent: user/chat scope is unavailable."

    task_name = task_name.strip()
    instructions = instructions.strip()

    coro = _run_subagent_task(chat_id, task_name, instructions)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        if _MAIN_LOOP and _MAIN_LOOP.is_running():
            asyncio.run_coroutine_threadsafe(coro, _MAIN_LOOP)
        else:
            LOGGER.error("Cannot launch subagent '%s': no event loop available", task_name)
            return "Could not launch background subagent: event loop unavailable."

    return (
        f"Background subagent launched for '{task_name}'. The subagent is now running independently "
        "in the background and will deliver its full report directly to the user in Telegram when done. "
        "Let the user know their task is running in the background and that they can continue chatting with you."
    )


SUBAGENT_TOOLS: list[BaseTool] = [spawn_subagent]

