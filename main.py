
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from dotenv import load_dotenv
import asyncio
import os

from memory import (
  PREFERENCE_TOOLS,
  add_memory,
  reset_current_user_id,
  search_memories,
  set_current_user_id,
)
from tool import TAVILY_TOOLS

load_dotenv(override=True)

# Initialize the Nebius model
nebius_model = ChatOpenAI(
    model=os.environ["llm"],
    base_url="https://api.tokenfactory.nebius.com/v1/",
    api_key=os.environ["NEBIUS_API_KEY"],
    streaming=True,
)

checkpointer = MemorySaver()

SYSTEM_PROMPT = """You are OYI, Sir Mario's personal AI assistant.

Reply directly in chat by default.

Preference memory:
- When the user clearly states a durable personal preference, call update_preference with a stable key,
  the current value, and source="conversation". Do not save temporary requests as preferences.

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

Web access (Tavily tools):
- Use tavily_search whenever information may be current or you do not already
  know the answer; then tavily_extract the most relevant source before quoting
  it. Always cite the URLs you relied on in your reply.
- Use tavily_map to list a site's pages, and tavily_crawl only when many pages
  of one site are needed — keep `limit` small, since every page costs credits.
- Use tavily_research only when the user explicitly asks for deep, long-form
  research. If it times out, resume with the returned request_id instead of
  starting a new task.
- If a tool reports a Tavily rate or usage limit, tell the user plainly instead
  of retrying immediately.
"""

agent = create_deep_agent(
    model=nebius_model,
    tools=[*TAVILY_TOOLS, *PREFERENCE_TOOLS],
    system_prompt=SYSTEM_PROMPT,
    checkpointer=checkpointer,
)

def get_config(thread_id: str = "me") -> dict:
    #returns a config dictionary for a given conversation thread.
    return {"configurable": {"thread_id": thread_id}}

def _prompt_with_memories(user_input: str, user_id: str) -> str:
  memories = search_memories(user_input, user_id)
  if not memories:
    return user_input
  return (
    "Relevant long-term memories about the user are below. Use them only when "
    "relevant and do not mention the memory system.\n\n"
    f"{memories}\n\nUser request:\n{user_input}"
  )


def _remember_exchange(user_input: str, response: str, user_id: str) -> None:
    if response.strip():
        add_memory(user_input, response, user_id)


def chat_agent(user_input: str, thread_id: str = "me") -> str:
    #returns the agent response to a given user input and conversation thread.
    token = set_current_user_id(thread_id)
    try:
        response = agent.invoke(
          {"messages": [{"role": "user", "content": _prompt_with_memories(user_input, thread_id)}]},
            config=get_config(thread_id),
        )
    finally:
        reset_current_user_id(token)
    answer = response["messages"][-1]["content"]
    _remember_exchange(user_input, answer, thread_id)
    return answer

def assistant_text_chunk(chunk) -> str:
    """Return visible assistant text while hiding tool and tool-call messages."""
    message_type = getattr(chunk, "type", "")
    if message_type not in {"AIMessage", "AIMessageChunk", "ai"}:
        return ""

    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    text_blocks = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text_blocks.append(str(block.get("text") or ""))
    return "".join(text_blocks)


def stream_chat_agent(user_input: str, thread_id: str = "me"):
    #yields text chunks synchronously.
    visible_text = []
    token = set_current_user_id(thread_id)
    try:
        for chunk, _ in agent.stream(
            {"messages": [{"role": "user", "content": _prompt_with_memories(user_input, thread_id)}]},
            config=get_config(thread_id),
            stream_mode="messages",
        ):
            text = assistant_text_chunk(chunk)
            if text:
                visible_text.append(text)
                yield text
    finally:
        reset_current_user_id(token)
    _remember_exchange(user_input, "".join(visible_text), thread_id)

async def astream_chat_agent(user_input: str, thread_id: str = "me"):
    #asynchronously yields text chunks from the agent without blocking the event loop.
    token = set_current_user_id(thread_id)
    try:
        prompt = await asyncio.to_thread(_prompt_with_memories, user_input, thread_id)
        visible_text = []
        async for chunk, _ in agent.astream(
            {"messages": [{"role": "user", "content": prompt}]},
            config=get_config(thread_id),
            stream_mode="messages",
        ):
            text = assistant_text_chunk(chunk)
            if text:
                visible_text.append(text)
                yield text
    finally:
        reset_current_user_id(token)
    await asyncio.to_thread(_remember_exchange, user_input, "".join(visible_text), thread_id)
