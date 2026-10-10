
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from dotenv import load_dotenv
import os

load_dotenv(override=True)

# Nebius is OpenAI-compatible — use ChatOpenAI with Nebius base URL.
# In .env set:
#   NEBIUS_API_KEY=<your token>
#   llm=nvidia/Nemotron-3_5-Lightning   (model name only, no prefix)
nebius_model = ChatOpenAI(
    model=os.environ["llm"],
    base_url="https://api.tokenfactory.nebius.com/v1/",
    api_key=os.environ["NEBIUS_API_KEY"],
    streaming=True,
)

checkpointer = MemorySaver()

SYSTEM_PROMPT = """You are OYI, Sir Mario's personal AI assistant.

Reply directly in chat by default.

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
"""

agent = create_deep_agent(
    model=nebius_model,
    system_prompt=SYSTEM_PROMPT,
    checkpointer=checkpointer,
)

def get_config(thread_id: str = "me") -> dict:
    #returns a config dictionary for a given conversation thread.
    return {"configurable": {"thread_id": thread_id}}

def chat_agent(user_input: str, thread_id: str = "me") -> str:
    #returns the agent response to a given user input and conversation thread.
    response = agent.invoke(
        {"messages": [{"role": "user", "content": user_input}]},
        config=get_config(thread_id),
    )
    return response["messages"][-1]["content"]

def stream_chat_agent(user_input: str, thread_id: str = "me"):
    #yields text chunks synchronously.
    for chunk, _ in agent.stream(
        {"messages": [{"role": "user", "content": user_input}]},
        config=get_config(thread_id),
        stream_mode="messages",
    ):
        if hasattr(chunk, "content") and chunk.content:
            yield chunk.content

async def astream_chat_agent(user_input: str, thread_id: str = "me"):
    #asynchronously yields text chunks from the agent without blocking the event loop.
    async for chunk, _ in agent.astream(
        {"messages": [{"role": "user", "content": user_input}]},
        config=get_config(thread_id),
        stream_mode="messages",
    ):
        if hasattr(chunk, "content") and chunk.content:
            yield chunk.content
