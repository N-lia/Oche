"""Bridge: expose a LangChain ``BaseTool`` as an Oche ``Tool``.

Lets plugins reuse the repo's existing tool modules (Tavily, voice, memory)
while they are ported. The legacy tools read the chat/user id from
``memory._CURRENT_USER_ID``, so we set it from the current turn while they run.
"""

from __future__ import annotations

import sys
from typing import Any

from oche.core import Tool
from oche.plugins.agent import current_turn


def _legacy_scope(thread_id: str | None):
    memory = sys.modules.get("memory")
    if memory is None or thread_id is None:
        return None, None
    return memory, memory.set_current_user_id(thread_id)


def from_langchain(lc_tool: Any, label: str = "") -> Tool:
    schema = lc_tool.args_schema
    if hasattr(schema, "model_json_schema"):
        parameters = schema.model_json_schema()
    elif isinstance(schema, dict):
        parameters = schema
    else:
        parameters = {"type": "object", "properties": {}}
    parameters.pop("title", None)

    async def run(**kwargs: Any) -> str:
        turn = current_turn()
        memory, token = _legacy_scope(turn.thread_id if turn else None)
        try:
            result = await lc_tool.ainvoke(kwargs)
        finally:
            if memory is not None:
                memory.reset_current_user_id(token)
        return result if isinstance(result, str) else str(getattr(result, "content", result))

    return Tool(name=lc_tool.name, description=lc_tool.description or "", parameters=parameters, run=run, label=label)
