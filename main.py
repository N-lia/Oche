"""Compatibility facade for the OYI agent service."""

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


assistant_text_chunk = agent_service.assistant_text_chunk
