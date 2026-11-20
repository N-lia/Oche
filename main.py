"""Compatibility facade: every frontend talks to the agent through here.

``OCHE_ENGINE=core`` routes turns through the plugin core in ``oche/``
(configured by ``oche.toml``). Anything else keeps the legacy DeepAgents
service in ``agent_service.py``.
"""

from __future__ import annotations

import asyncio
import os

ENGINE = os.environ.get("OCHE_ENGINE", "legacy").strip().lower()

if ENGINE == "core":
    from dotenv import load_dotenv

    from oche import create_app

    load_dotenv(override=True)
    _app = None
    _app_lock = asyncio.Lock()

    async def get_app():
        """The process-wide Oche app, started on first use inside the running loop."""
        global _app
        async with _app_lock:
            if _app is None:
                _app = await create_app().start()
        return _app

    def get_config(thread_id: str = "me") -> dict:
        return {"configurable": {"thread_id": thread_id}}

    async def astream_events(user_input: str, thread_id: str = "me"):
        app = await get_app()
        async for event in app.agent.run(user_input, thread_id):
            yield event

    async def astream_chat_agent(user_input: str, thread_id: str = "me"):
        async for event in astream_events(user_input, thread_id):
            if event["type"] == "token":
                yield event["text"]

    def chat_agent(user_input: str, thread_id: str = "me") -> str:
        async def run() -> str:
            return "".join([chunk async for chunk in astream_chat_agent(user_input, thread_id)])

        return asyncio.run(run())

    def stream_chat_agent(user_input: str, thread_id: str = "me"):
        yield chat_agent(user_input, thread_id)

else:
    from agent_service import agent_service

    def get_config(thread_id: str = "me") -> dict:
        return agent_service.get_config(thread_id)

    def chat_agent(user_input: str, thread_id: str = "me") -> str:
        return agent_service.chat(user_input, thread_id)

    def stream_chat_agent(user_input: str, thread_id: str = "me"):
        yield from agent_service.stream(user_input, thread_id)

    async def astream_chat_agent(user_input: str, thread_id: str = "me"):
        async for chunk in agent_service.astream(user_input, thread_id):
            yield chunk

    async def astream_events(user_input: str, thread_id: str = "me"):
        async for event in agent_service.astream_events(user_input, thread_id):
            yield event

    assistant_text_chunk = agent_service.assistant_text_chunk
