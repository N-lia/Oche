"""
FastAPI Backend Server for OYI Telegram Voice WebApp.

Orchestrates:
1. Parakeet Real-Time EOU 120M (Speech-to-Text & End-Of-Utterance detection)
2. OYI Agent Service (Conversational LLM with memory & tools)
3. Chatterbox-Turbo (Expressive Cloned Voice TTS on Modal GPU)

Run locally:
    python webapp_server.py
"""

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_service import agent_service
from asr_client import atranscribe, health as asr_health
from telegram_format import normalize_agent_html, voice_text_for_speech
from backchannel import (
    CATEGORIES,
    all_phrases,
    cached_clips,
    clip_key,
    clip_path,
    prewarm,
)
from main import astream_chat_agent
from voice import get_tts_provider, get_tts_url, synthesize_speech
from voice_session import VoiceSession

load_dotenv()

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("oyi.webapp")

app = FastAPI(title="OYI Live Chat", version="2.0.0")


@app.on_event("startup")
async def _maybe_enable_loop_debug() -> None:
    """WEBAPP_DEBUG_LOOP=1 turns on asyncio slow-callback warnings.

    Any callback blocking the event loop >0.5s is logged with its stack, which
    is how we hunt the stalls that used to trip WebSocket keepalive timeouts.
    """
    if os.getenv("WEBAPP_DEBUG_LOOP", "0") != "1":
        return
    loop = asyncio.get_running_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = 0.5
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    LOGGER.warning("asyncio loop debug enabled (slow_callback_duration=0.5s)")

    # SIGUSR1 dumps every thread's stack to the log: `docker kill -s USR1 webapp`
    # after a hang shows what the agent task is really awaiting.
    import faulthandler
    import signal
    import sys

    # Fatal-signal stacks: if the process segfaults (exit 139), the next boot's
    # log starts with the dump naming the thread and frames that died.
    faulthandler.enable(file=sys.stderr, all_threads=True)
    faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
    LOGGER.warning("faulthandler crash+SIGUSR1 dumps registered")

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


def decode_audio_base64(audio_base64: str) -> bytes:
    try:
        return base64.b64decode(audio_base64, validate=False)
    except Exception as exc:  # noqa: BLE001 - reported to the client
        raise HTTPException(status_code=400, detail=f"Invalid base64 audio: {exc}") from exc


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
    """Transcribe audio and detect End-of-Utterance via the local Parakeet gRPC service."""
    LOGGER.info("Calling Parakeet EOU over gRPC")
    result = await atranscribe(decode_audio_base64(req.audio_base64))
    if result.error:
        LOGGER.error("Parakeet ASR error: %s", result.error)
        raise HTTPException(status_code=502, detail=f"Parakeet ASR error: {result.error}")
    return {
        "text": result.text,
        "eou_detected": result.eou_detected,
        "raw": result.raw,
        "duration_s": result.duration_s,
    }


@app.get("/api/health")
async def api_health():
    """Service status for the WebApp header (ASR reachability, TTS config)."""
    asr_ok = False
    asr_detail = ""
    smart_turn = False
    try:
        status = await asyncio.to_thread(asr_health)
        asr_ok = bool(getattr(status, "ready", False))
        asr_detail = getattr(status, "model", "") or getattr(status, "detail", "")
        smart_turn = bool(getattr(status, "smart_turn_ready", False))
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        asr_detail = str(exc)

    return {
        "ok": True,
        "version": app.version,
        "asr": {"ready": asr_ok, "detail": asr_detail, "smart_turn": smart_turn},
        "tts": {
            "configured": bool(get_tts_url()),
            "provider": get_tts_provider(),
        },
        "backchannels": {"cached": len(cached_clips())},
    }


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """Run text prompt through OYI agent."""
    if not req.prompt.strip():
        return {"response": ""}
    try:
        reply = await asyncio.to_thread(agent_service.chat, req.prompt, req.thread_id)
        # Text bubbles go through the same canonicality pass as voice turns:
        # one HTML format everywhere, diagrams always mountable in the miniapp.
        return {"response": normalize_agent_html(reply)}
    except Exception as exc:
        LOGGER.exception("Agent chat failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/voice-turn")
async def api_voice_turn(req: VoiceTurnRequest):
    """
    Unified Voice Turn Endpoint:
    1. Audio -> Parakeet Real-Time EOU (Transcribe + Detect <EOU>) over gRPC
    2. Prompt -> OYI Agent LLM (or echo if in echo mode)
    3. Response -> Chatterbox-Turbo (Synthesize Cloned Voice on Modal)
    """
    # 1. Transcribe with Parakeet EOU
    result = await atranscribe(decode_audio_base64(req.audio_base64))
    if result.error:
        LOGGER.error("Parakeet failed: %s", result.error)
        raise HTTPException(status_code=502, detail=f"Parakeet EOU error: {result.error}")

    user_text = result.text.strip()
    eou_detected = result.eou_detected

    if not user_text:
        return {
            "user_text": "",
            "bot_text": "I didn't catch that. Could you repeat?",
            "eou_detected": False,
            "audio_base64": "",
        }

    # 2. Generate response text with LLM
    if req.mode == "echo":
        bot_text = f"[Echo test] You said: {user_text}"
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

    # 3. Synthesize voice with the configured provider
    audio_base64 = ""
    try:
        spoken = voice_text_for_speech(bot_text)
        audio_bytes = await synthesize_speech(spoken, mood=req.mood, event="chat")
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as exc:
        LOGGER.exception("Chatterbox TTS failed")

    return {
        "user_text": user_text,
        # Same one-format pass as /api/chat: bubbles render canonical HTML.
        "bot_text": normalize_agent_html(bot_text),
        "eou_detected": eou_detected,
        "audio_base64": audio_base64,
    }


# ---------------------------------------------------------------------------
# Backchannel clip library
# ---------------------------------------------------------------------------

def _clip_url(phrase: str) -> str:
    return f"/api/backchannels/{clip_key(phrase)}.mp3"


@app.get("/api/backchannels")
async def api_backchannels():
    """Manifest of backchannel phrases and any clips already cached."""
    cached = {phrase: _clip_url(phrase) for phrase in cached_clips()}
    return {
        "phrases": all_phrases(),
        "categories": CATEGORIES,
        "cached": cached,
    }


@app.post("/api/backchannels/prewarm")
async def api_backchannels_prewarm():
    """Synthesize any missing backchannel clips (can be slow on first run)."""
    status = await prewarm()
    return {"status": status}


_PREWARM_STARTED = False


def _schedule_prewarm() -> None:
    """Kick off background clip synthesis once per process."""
    global _PREWARM_STARTED
    if _PREWARM_STARTED:
        return
    _PREWARM_STARTED = True

    async def _run() -> None:
        try:
            await prewarm()
        except Exception:
            LOGGER.exception("Backchannel prewarm failed")

    asyncio.create_task(_run())


@app.get("/api/backchannels/{clip_id}.mp3")
async def api_backchannel_clip(clip_id: str):
    for phrase in all_phrases():
        if clip_key(phrase) == clip_id and clip_path(phrase).exists():
            return Response(content=clip_path(phrase).read_bytes(), media_type="audio/mpeg")
    raise HTTPException(status_code=404, detail="Backchannel clip not found")


# ---------------------------------------------------------------------------
# Live voice session (WebSocket)
# ---------------------------------------------------------------------------


async def _drain(websocket: WebSocket, out: "asyncio.Queue[dict | None]") -> None:
    try:
        while True:
            message = await out.get()
            if message is None:
                return
            await websocket.send_json(message)
    except Exception:
        return


@app.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket):
    """Live streaming voice loop with backchannels and thinking fillers."""
    await websocket.accept()
    out: asyncio.Queue[dict | None] = asyncio.Queue()
    smart_turn = False
    try:
        status = await asyncio.to_thread(asr_health)
        smart_turn = bool(getattr(status, "smart_turn_ready", False))
    except Exception:  # noqa: BLE001 - optional capability
        LOGGER.debug("ASR health probe failed for Smart Turn capability", exc_info=True)
    session = VoiceSession(out, clip_url=_clip_url, smart_turn_available=smart_turn)
    sender = asyncio.create_task(_drain(websocket, out))
    _schedule_prewarm()  # warm clip cache in the background

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if (data := message.get("bytes")) is not None:
                await session.on_audio(data)
            elif (text := message.get("text")) is not None:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                await session.on_control(payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        LOGGER.exception("Voice websocket failed")
    finally:
        await out.put(None)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        # Releases the ASR stream even if the client vanished mid-turn.
        await session.aclose()


if __name__ == "__main__":
    port = int(os.getenv("WEBAPP_PORT", "8000"))
    reload = os.getenv("WEBAPP_RELOAD", "0") == "1"
    # WebSocket keepalive: browsers never send pings, and a >20s stall during
    # agent/TTS would otherwise trip uvicorn's default 20s ping timeouts and
    # close the voice socket mid-turn. Disabled by default; opt back in via
    # WEBAPP_WS_PING_INTERVAL / WEBAPP_WS_PING_TIMEOUT (seconds).
    def _ws_env(name: str, default: str | None) -> float | None:
        raw = os.getenv(name)
        if raw is None:
            return None if default is None else float(default)
        raw = raw.strip()
        if not raw or raw == "0":
            return None
        return float(raw)

    ws_ping_interval = _ws_env("WEBAPP_WS_PING_INTERVAL", None)
    ws_ping_timeout = _ws_env("WEBAPP_WS_PING_TIMEOUT", None)
    LOGGER.info(
        "Starting OYI Live Chat on http://0.0.0.0:%d (reload=%s, ws_ping_interval=%s)",
        port,
        reload,
        ws_ping_interval,
    )
    uvicorn.run(
        "webapp_server:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        # Plain asyncio on purpose: uvloop segfaults inside openai/httpx SSE
        # reads here (openai/_streaming.py __aiter__ -> exit 139,Fatal Python
        # error), while the identical stream on the stdlib loop never crashed.
        loop="asyncio",
        proxy_headers=True,
        forwarded_allow_ips="*",
        ws_ping_interval=ws_ping_interval,
        ws_ping_timeout=ws_ping_timeout,
    )

