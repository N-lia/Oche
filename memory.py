"""Persistent long-term memory for the Telegram agent."""

from __future__ import annotations

import logging
import json
import os
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
            "config": {
                "path": os.path.join(data_dir, "qdrant"),
                "collection_name": DEFAULT_MEMORY_COLLECTION,
                "embedding_model_dims": DEFAULT_EMBEDDING_DIMENSIONS,
            },
        },
        "history_db_path": os.path.join(data_dir, "history.db"),
    }
    return Memory.from_config(config)


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
    result = _get_memory().search(key.strip(), filters={"user_id": user_id}, top_k=20)
    matches = [
        memory
        for memory in _memory_results(result)
        if (memory.get("metadata") or {}).get("key") == key.strip()
    ]
    return _sort_recent(matches)[0] if matches else None


def search_memories(query: str, user_id: str, limit: int = 6) -> str:
    """Return concise relevant memories, or an empty string when unavailable."""
    try:
        result = _get_memory().search(query, filters={"user_id": user_id}, top_k=limit)
        memories = _sort_recent(_memory_results(result))
        return "\n".join(
            f"- {item.get('memory', '').strip()}"
            for item in memories
            if isinstance(item, dict) and item.get("memory")
        )
    except Exception:
        LOGGER.exception("Long-term memory search failed")
        return ""


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