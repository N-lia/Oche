"""Persistent long-term memory for the Telegram agent."""

from __future__ import annotations

import logging
import json
import os
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Literal, cast

from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr

from router import DAILY_MODEL

LOGGER = logging.getLogger("oyi.memory")
DEFAULT_DATA_DIR = "./data/oyi-memory"
DEFAULT_MEMORY_COLLECTION = "oyi_memories"
DEFAULT_EMBEDDING_DIMENSIONS = 4096
PREFERENCE_SOURCE = "agent"
_CURRENT_USER_ID: ContextVar[str | None] = ContextVar("oyi_current_user_id", default=None)


class UpdatePreferenceInput(BaseModel):
    key: str = Field(..., min_length=1, description="Stable preference name, such as response_style.")
    value: str = Field(..., min_length=1, description="The user's current preference value.")
    source: str = Field(PREFERENCE_SOURCE, min_length=1, description="Where this preference came from.")


class ReconciliationAction(BaseModel):
    action: Literal["update", "delete"]
    memory_id: str
    key: str
    value: str | None = None
    reason: str


class ReconciliationPlan(BaseModel):
    actions: list[ReconciliationAction] = Field(default_factory=list)


def set_current_user_id(user_id: str) -> Any:
    """Set the user scope used by agent tools and return a reset token."""
    return _CURRENT_USER_ID.set(user_id)


def reset_current_user_id(token: Any) -> None:
    _CURRENT_USER_ID.reset(token)


@lru_cache(maxsize=1)
def _get_memory():
    data_dir = os.getenv("MEM0_DATA_DIR", DEFAULT_DATA_DIR)
    os.makedirs(data_dir, exist_ok=True)
    mem0_dir = os.environ.get("MEM0_DIR") or os.path.join(data_dir, ".mem0")
    os.environ["MEM0_DIR"] = mem0_dir
    os.makedirs(mem0_dir, exist_ok=True)

    from mem0 import Memory

    config = {
        "llm": {
            "provider": "openai",
            "config": {
                "model": DAILY_MODEL,
                "api_key": os.environ["NEBIUS_API_KEY"],
                "openai_base_url": "https://api.tokenfactory.nebius.com/v1/",
                "temperature": 0.1,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": "Qwen/Qwen3-Embedding-8B",
                "api_key": os.environ["NEBIUS_API_KEY"],
                "openai_base_url": "https://api.tokenfactory.nebius.com/v1/",
                "embedding_dims": DEFAULT_EMBEDDING_DIMENSIONS,
            },
        },
        "vector_store": {
            "provider": "qdrant",
            "config": _vector_store_config(data_dir),
        },
        "history_db_path": os.path.join(data_dir, "history.db"),
    }
    return Memory.from_config(config)


def _vector_store_config(data_dir: str) -> dict[str, Any]:
    """Use one Qdrant server when configured, else the local directory.

    Local Qdrant mode takes an exclusive file lock, so two processes (the
    Telegram bot and the WebApp) cannot share one directory.  Setting
    ``QDRANT_URL`` lets both channels read and write the same memories.
    """
    config: dict[str, Any] = {
        "collection_name": DEFAULT_MEMORY_COLLECTION,
        "embedding_model_dims": DEFAULT_EMBEDDING_DIMENSIONS,
    }
    url = os.getenv("QDRANT_URL", "").strip()
    if url:
        config["url"] = url
        api_key = os.getenv("QDRANT_API_KEY", "").strip()
        if api_key:
            config["api_key"] = api_key
    else:
        config["path"] = os.path.join(data_dir, "qdrant")
    return config


def _memory_results(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        result = result.get("results", [])
    return [item for item in result if isinstance(item, dict)]


def _updated_at(memory: dict[str, Any]) -> str:
    metadata = memory.get("metadata") or {}
    return str(metadata.get("updated_at") or memory.get("updated_at") or "")


def _sort_recent(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(memories, key=_updated_at, reverse=True)


def _preference_metadata(key: str, value: str, source: str) -> dict[str, str]:
    return {
        "key": key.strip(),
        "value": value.strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": source.strip(),
    }


def _preference_memories(user_id: str) -> list[dict[str, Any]]:
    result = _get_memory().get_all(filters={"user_id": user_id}, top_k=100)
    return [
        memory
        for memory in _memory_results(result)
        if isinstance(memory.get("metadata"), dict) and memory["metadata"].get("key")
    ]


def _find_preference(key: str, user_id: str) -> dict[str, Any] | None:
    wanted = key.strip().lower()
    matches = [
        memory
        for memory in _preference_memories(user_id)
        if str((memory.get("metadata") or {}).get("key") or "").strip().lower() == wanted
    ]
    return _sort_recent(matches)[0] if matches else None


def _score(memory: dict[str, Any]) -> float:
    value = memory.get("score")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _is_preference(memory: dict[str, Any]) -> bool:
    metadata = memory.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("key"))


def _memory_text(memory: dict[str, Any]) -> str:
    return str(memory.get("memory") or memory.get("data") or "").strip()


def warm_memory() -> bool:
    """Initialise the memory backend before the first user turn.

    Mem0's first call constructs the embedder, Qdrant collection, and history
    DB.  That one-time cost can exceed the per-turn memory timeout, which would
    make the very first reply ignore the user's stored name and preferences.
    """
    try:
        _get_memory()
        return True
    except Exception:
        LOGGER.exception("Long-term memory warm-up failed")
        return False


def search_memories(
    query: str,
    user_id: str,
    limit: int = 6,
    min_score: float = 0.25,
) -> str:
    """Return concise, *relevance-ranked* memories for the query.

    Mem0 returns a cosine ``score`` with each hit.  Sorting by ``updated_at``
    (the old behaviour) discarded that signal, so a recent but unrelated memory
    could displace the fact the user actually asked about.
    """
    try:
        result = _get_memory().search(query, filters={"user_id": user_id}, top_k=max(limit, 6))
        memories = sorted(_memory_results(result), key=_score, reverse=True)
        lines: list[str] = []
        seen: set[str] = set()
        for item in memories:
            text = _memory_text(item)
            if not text or text in seen:
                continue
            # Structured preferences are always relevant; free-form memories
            # need a real semantic match, otherwise random old facts leak in.
            if not _is_preference(item) and _score(item) < min_score:
                continue
            seen.add(text)
            lines.append(f"- {text}")
            if len(lines) >= limit:
                break
        return "\n".join(lines)
    except Exception:
        LOGGER.exception("Long-term memory search failed")
        return ""


def memory_user_id(thread_id: str) -> str:
    """Map a channel/thread to the primary user's durable memory scope.

    Telegram threads are already the numeric user id.  The WebApp and the
    Android client use friendly thread names, so without this mapping their
    memory would be a separate empty store even though it is the same person.
    """
    thread = (thread_id or "").strip()
    if thread and thread not in {"me", "webapp", "android", "phone", "default"}:
        return thread
    return (
        os.getenv("OYI_PRIMARY_USER_ID", "").strip()
        or os.getenv("My_Telegram_ID", "").strip()
        or os.getenv("TELEGRAM_CHAT_ID", "").strip()
        or thread
        or "me"
    )


def build_memory_context(user_id: str, query: str = "", max_items: int = 8) -> str:
    """Build the compact memory block injected into every agent prompt.

    Structured preferences (name, response style, …) are always included, even
    when the current question has no semantic overlap with them.  Free-form
    memories are included only when they clear the relevance threshold.
    """
    query = (query or "").strip()
    blocks: list[str] = []
    seen: set[str] = set()

    try:
        preferences = sorted(_preference_memories(user_id), key=_updated_at, reverse=True)
    except Exception:
        LOGGER.exception("Preference lookup failed user_id=%s", user_id)
        preferences = []

    preference_lines: list[str] = []
    for memory in preferences:
        metadata = memory.get("metadata") or {}
        key = str(metadata.get("key") or "").strip()
        value = str(metadata.get("value") or "").strip()
        if not key or not value:
            continue
        line = f"- {key}: {value}"
        if line in seen:
            continue
        seen.add(line)
        preference_lines.append(line)
    if preference_lines:
        blocks.append("Known preferences:\n" + "\n".join(preference_lines))

    relevant_lines: list[str] = []
    if query:
        try:
            result = _get_memory().search(query, filters={"user_id": user_id}, top_k=max(max_items * 2, 10))
            for item in sorted(_memory_results(result), key=_score, reverse=True):
                if _is_preference(item) or _score(item) < 0.35:
                    continue
                text = _memory_text(item)
                if not text or "system context" in text.lower():
                    continue
                line = f"- {text[:400]}"
                if line in seen:
                    continue
                seen.add(line)
                relevant_lines.append(line)
                if len(relevant_lines) >= max_items:
                    break
        except Exception:
            LOGGER.exception("Relevant memory lookup failed user_id=%s", user_id)
    if relevant_lines:
        blocks.append("Relevant memories:\n" + "\n".join(relevant_lines))

    return "\n\n".join(blocks)


def add_memory(user_input: str, assistant_response: str, user_id: str) -> None:
    """Let Mem0 extract and persist durable facts from a completed exchange."""
    try:
        _get_memory().add(
            [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": assistant_response},
            ],
            user_id=user_id,
        )
    except Exception:
        LOGGER.exception("Long-term memory update failed")


_REMEMBER_RE = re.compile(
    r"\b(?:remember(?:\s+that|\s+this)?|keep\s+in\s+mind|for\s+future\s+reference|"
    r"note\s+that|save\s+this)\b",
    re.IGNORECASE,
)
_FACT_RE = re.compile(
    r"\b(?:my\s+name\s+is|call\s+me|i\s+prefer|i\s+like|i\s+love|i\s+hate|"
    r"i\s+don'?t\s+like|my\s+favou?rite|i\s+live|i\s+work|i\s+study|i\s+use|"
    r"i\s+own|i\s+have|i\s+am\s+(?:a|an|the)|i'?m\s+(?:a|an|the)|"
    r"i'?m\s+building|my\s+(?:project|company|job|birthday|email|phone|goal|"
    r"address|team|wife|husband|partner|child|children))\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"^\s*(?:what|who|where|when|why|how|do|does|did|can|could|is|are|will|"
    r"would|should|have|has|tell\s+me\s+about)\b",
    re.IGNORECASE,
)
_TRANSIENT_RE = re.compile(
    r"\b(?:today|tonight|right\s+now|at\s+the\s+moment|currently|this\s+morning|"
    r"this\s+afternoon|this\s+evening|tired|hungry|bored|angry|sad|happy)\b",
    re.IGNORECASE,
)


def should_remember(user_input: str, intent: str = "") -> bool:
    """Return True when a turn is likely to contain a durable personal fact.

    This is a cheap filter, not the memory extractor itself.  It decides
    whether the completed exchange is worth sending to Mem0 in the background,
    so the common case (ordinary Q&A) never pays the extraction cost.
    """
    text = (user_input or "").strip()
    if len(text) < 12:
        return False
    if intent == "preference":
        return True
    if _REMEMBER_RE.search(text):
        return True
    if text.endswith("?") or _QUESTION_RE.match(text):
        return False
    if _TRANSIENT_RE.search(text):
        return False
    return bool(_FACT_RE.search(text))


class SearchMemoryInput(BaseModel):
    query: str = Field(..., min_length=1, description="Keywords or topic to search for in user's long-term memory.")


class SaveMemoryInput(BaseModel):
    fact: str = Field(..., min_length=1, description="Durable personal fact or detail about the user to remember.")


@tool(args_schema=SearchMemoryInput)
def recall_memory(query: str) -> str:
    """Search long-term memory for previously recorded facts, preferences, personal details, or projects."""
    user_id = _CURRENT_USER_ID.get()
    if not user_id:
        return "Memory scope is unavailable."
    memories = search_memories(query, user_id, limit=5)
    return memories if memories else "No relevant memories found in long-term storage."


@tool(args_schema=SaveMemoryInput)
def save_memory(fact: str) -> str:
    """Save a durable fact, project detail, or background context about Sir Mario to long-term memory. Do not call this for temporary conversational chit-chat."""
    user_id = _CURRENT_USER_ID.get()
    if not user_id:
        return "Memory scope is unavailable."
    try:
        _get_memory().add(
            [{"role": "user", "content": fact.strip()}],
            user_id=user_id,
            infer=False,
        )
        return f"Saved to memory: '{fact.strip()}'"
    except Exception:
        LOGGER.exception("Failed to save memory fact=%s user_id=%s", fact, user_id)
        return "Could not save that memory."


@tool(args_schema=UpdatePreferenceInput)
def update_preference(key: str, value: str, source: str = PREFERENCE_SOURCE) -> str:
    """Create or replace one preference identified by its stable key."""
    user_id = _CURRENT_USER_ID.get()
    key, value, source = key.strip(), value.strip(), source.strip()
    if not user_id:
        return "Preference scope is unavailable."
    if not key or not value or not source:
        return "Preference key, value, and source must be non-empty."

    try:
        existing = _find_preference(key, user_id)
        metadata = _preference_metadata(key, value, source)
        text = f"{key}: {value}"
        if existing:
            _get_memory().update(existing["id"], text=text, metadata=metadata)
            return f"Updated preference '{key}'."

        _get_memory().add(
            [{"role": "user", "content": text}],
            user_id=user_id,
            metadata=metadata,
            infer=False,
        )
        return f"Saved preference '{key}'."
    except Exception:
        LOGGER.exception("Preference update failed key=%s user_id=%s", key, user_id)
        return "Could not update that preference."


MEMORY_TOOLS: list[BaseTool] = [recall_memory, save_memory, update_preference]
PREFERENCE_TOOLS: list[BaseTool] = MEMORY_TOOLS


@lru_cache(maxsize=1)
def _get_reconciliation_model():
    return ChatOpenAI(
        model=DAILY_MODEL,
        base_url="https://api.tokenfactory.nebius.com/v1/",
        api_key=SecretStr(os.environ["NEBIUS_API_KEY"]),
        temperature=0,
    ).with_structured_output(ReconciliationPlan)


def reconcile_preferences(user_id: str) -> dict[str, int]:
    """Ask Nemotron to resolve contradictory preference records."""
    try:
        preferences = _sort_recent(_preference_memories(user_id))
        if len(preferences) < 2:
            return {"updated": 0, "deleted": 0}

        prompt = (
            "Review these preference records for one user. Find only clear contradictions "
            "or duplicate keys with conflicting values. Keep the newest trustworthy value. "
            "For contradictions, return update and/or delete actions. Return no action when "
            "records are compatible. Never invent preferences or change unrelated records.\n\n"
            f"{json.dumps(preferences, default=str)}"
        )
        plan = cast(ReconciliationPlan, _get_reconciliation_model().invoke(prompt))
        by_id = {memory.get("id"): memory for memory in preferences}
        updated = deleted = 0
        for action in plan.actions:
            memory = by_id.get(action.memory_id)
            metadata = (memory or {}).get("metadata") or {}
            if not memory or metadata.get("key") != action.key:
                continue
            if action.action == "delete":
                _get_memory().delete(action.memory_id)
                deleted += 1
            elif action.value and action.value.strip():
                _get_memory().update(
                    action.memory_id,
                    text=f"{action.key}: {action.value.strip()}",
                    metadata=_preference_metadata(action.key, action.value, "reconciliation"),
                )
                updated += 1
        return {"updated": updated, "deleted": deleted}
    except Exception:
        LOGGER.exception("Preference reconciliation failed user_id=%s", user_id)
        return {"updated": 0, "deleted": 0}