"""
FastAPI Backend Server for OYI Telegram Voice WebApp.

Orchestrates:
1. Parakeet Real-Time EOU 120M (Speech-to-Text & End-Of-Utterance detection)
2. OYI Agent Service (Conversational LLM with memory & tools)
3. Chatterbox-Turbo (Expressive Cloned Voice TTS on Modal GPU)

Run locally:
    python webapp_server.py
"""

import base64
import logging
import os
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_service import agent_service
from main import astream_chat_agent
from voice import synthesize_speech

load_dotenv()

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("oyi.webapp")

app = FastAPI(title="OYI Voice Lab WebApp", version="1.0.0")

# CORS middleware for WebApp access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "webapp"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_parakeet_url() -> str:
    url = os.getenv("PARAKEET_MODAL_URL", "").strip().rstrip("/")
    if not url:
        return ""
    return url if "modal.run" in url else f"{url}/transcribe"


class TranscribeRequest(BaseModel):
    audio_base64: str


class ChatRequest(BaseModel):
    prompt: str
    thread_id: str = "webapp"


class VoiceTurnRequest(BaseModel):
    audio_base64: str
    mode: str = "agent"  # "agent" | "echo"
    mood: str = "normal"  # "normal" | "cheerful" | "angry"
    thread_id: str = "webapp"


@app.get("/")
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="WebApp index.html not found")
    return FileResponse(index_file)


@app.post("/api/transcribe")
async def api_transcribe(req: TranscribeRequest):
    """Transcribe audio chunk and detect End-of-Utterance with Parakeet Real-Time EOU."""
    endpoint = get_parakeet_url()
    if not endpoint:
        raise HTTPException(
            status_code=500,
            detail="PARAKEET_MODAL_URL is not configured in .env. Deploy modal_parakeet.py and add its URL to .env.",
        )

    LOGGER.info("Calling Parakeet EOU at %s", endpoint)

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(endpoint, json={"audio_base64": req.audio_base64})
        if res.status_code != 200:
            LOGGER.error("Parakeet ASR error: %s", res.text)
            raise HTTPException(status_code=res.status_code, detail=f"Parakeet ASR error: {res.text}")
        return res.json()


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """Run text prompt through OYI agent."""
    if not req.prompt.strip():
        return {"response": ""}
    try:
        reply = await agent_service.achat(req.prompt, thread_id=req.thread_id)
        return {"response": reply}
    except Exception as exc:
        LOGGER.exception("Agent chat failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/voice-turn")
async def api_voice_turn(req: VoiceTurnRequest):
    """
    Unified Voice Turn Endpoint:
    1. Audio -> Parakeet Real-Time EOU (Transcribe + Detect <EOU>)
    2. Prompt -> OYI Agent LLM (or echo if in echo mode)
    3. Response -> Chatterbox-Turbo (Synthesize Cloned Voice)
    """
    endpoint = get_parakeet_url()
    if not endpoint:
        raise HTTPException(
            status_code=500,
            detail="PARAKEET_MODAL_URL is not configured. Please deploy modal_parakeet.py first.",
        )

    # 1. Transcribe with Parakeet EOU
    async with httpx.AsyncClient(timeout=30.0) as client:
        asr_res = await client.post(
            endpoint,
            json={"audio_base64": req.audio_base64},
        )
        if asr_res.status_code != 200:
            LOGGER.error("Parakeet failed: %s", asr_res.text)
            raise HTTPException(status_code=500, detail=f"Parakeet EOU error: {asr_res.text}")

        asr_data = asr_res.json()
        user_text = asr_data.get("text", "").strip()
        eou_detected = asr_data.get("eou_detected", False)

    if not user_text:
        return {
            "user_text": "",
            "bot_text": "I didn't catch that. Could you repeat?",
            "eou_detected": False,
            "audio_base64": "",
        }

    # 2. Generate response text with LLM
    if req.mode == "echo":
        bot_text = f"You said: {user_text}"
    else:
        try:
            LOGGER.info("Triggering OYI LLM agent for user_text: '%s' (thread_id=%s)", user_text, req.thread_id)
            chunks = []
            async for chunk in astream_chat_agent(user_text, thread_id=req.thread_id):
                if chunk:
                    chunks.append(chunk)
            bot_text = "".join(chunks).strip() or "I could not produce a response."
            LOGGER.info("OYI LLM response: '%s'", bot_text[:100])
        except Exception as exc:
            LOGGER.exception("Agent service chat failed")
            bot_text = f"Agent error: {exc}"

    # 3. Synthesize voice with Chatterbox Turbo
    audio_base64 = ""
    try:
        audio_bytes = await synthesize_speech(bot_text, mood=req.mood, event="chat")
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as exc:
        LOGGER.exception("Chatterbox TTS failed")

    return {
        "user_text": user_text,
        "bot_text": bot_text,
        "eou_detected": eou_detected,
        "audio_base64": audio_base64,
    }


if __name__ == "__main__":
    port = int(os.getenv("WEBAPP_PORT", "8000"))
    LOGGER.info("Starting OYI Voice WebApp on http://0.0.0.0:%d", port)
    uvicorn.run("webapp_server:app", host="0.0.0.0", port=port, reload=True)

