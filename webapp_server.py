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
import secrets
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_service import agent_service
from asr_client import atranscribe, health as asr_health
from device_gateway import (
    DeviceConnection,
    DeviceGatewayError,
    DeviceOfflineError,
    device_gateway,
)
from device_protocol import CommandRequest, DeviceHello
from sandbox_planner import run_sandbox_planner, sandbox_planner_enabled
from telegram_format import PlainTextStream, strip_telegram_html
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


@app.on_event("shutdown")
async def _shutdown_device_gateway() -> None:
    await device_gateway.aclose()

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


def _device_api_token() -> str:
    return (
        os.getenv("OYI_DEVICE_API_TOKEN", "").strip()
        or os.getenv("OYI_DEVICE_TOKEN", "").strip()
    )


def _require_device_api(authorization: str | None) -> None:
    expected = _device_api_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Device API is not configured. Set OYI_DEVICE_API_TOKEN (or OYI_DEVICE_TOKEN).",
        )
    supplied = (authorization or "").strip()
    if not supplied.startswith("Bearer ") or not secrets.compare_digest(
        supplied.removeprefix("Bearer ").strip(), expected
    ):
        raise HTTPException(status_code=401, detail="Invalid device API token.")


def _device_ws_token_ok(websocket: WebSocket) -> bool:
    expected = os.getenv("OYI_DEVICE_TOKEN", "").strip() or _device_api_token()
    if not expected:
        return False
    supplied = websocket.headers.get("authorization", "").strip()
    if supplied.startswith("Bearer "):
        supplied = supplied.removeprefix("Bearer ").strip()
    else:
        supplied = websocket.query_params.get("token", "").strip()
    return bool(supplied) and secrets.compare_digest(supplied, expected)


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
    # The agent writes Telegram HTML. Native clients (the Android app) ask for
    # plain text so they do not have to render markup.
    plain: bool = False


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


@app.get("/api/device/health")
async def api_device_health(authorization: str | None = Header(default=None)):
    """Health/capability probe for the Android app and channel adapters."""
    _require_device_api(authorization)
    devices = device_gateway.list_devices()
    return {
        "ok": True,
        "model": os.getenv("OYI_DEVICE_VISION_MODEL", "nvidia/Nemotron-3.5-Super-VL"),
        "vision_configured": bool(os.getenv("NEBIUS_API_KEY", "").strip()),
        "devices_connected": sum(1 for device in devices if device.connected),
        "devices": [device.model_dump() for device in devices],
    }


@app.get("/api/device/devices")
async def api_device_devices(authorization: str | None = Header(default=None)):
    _require_device_api(authorization)
    return {"devices": [device.model_dump() for device in device_gateway.list_devices()]}


@app.get("/api/device/status")
async def api_device_status(
    device_id: str = "",
    authorization: str | None = Header(default=None),
):
    """Ask the phone what it is doing right now (current app + usage)."""
    _require_device_api(authorization)
    try:
        return await device_gateway.request_status(device_id=device_id)
    except (DeviceGatewayError, DeviceOfflineError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/device/job")
async def api_device_job(
    req: CommandRequest,
    authorization: str | None = Header(default=None),
):
    """Start a phone command whose planner runs outside this process.

    Used by the Nebius Sandbox planner: the sandbox drives the decision loop
    and the gateway stays the transport hub for the phone.
    """
    _require_device_api(authorization)
    job = await device_gateway.create_job(
        device_id=req.device_id,
        instruction=req.text,
        max_steps=req.max_steps,
    )
    return {"job_id": job.id, "device_id": job.device_id}


@app.get("/api/device/job/{job_id}/observation")
async def api_device_job_observation(
    job_id: str,
    authorization: str | None = Header(default=None),
):
    """The next screen for the planner (long poll)."""
    _require_device_api(authorization)
    try:
        return await device_gateway.next_observation(job_id)
    except DeviceGatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/device/job/{job_id}/action")
async def api_device_job_action(
    job_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
):
    """The planner's raw model output; the gateway validates and executes it."""
    _require_device_api(authorization)
    raw = str(payload.get("raw") or "")
    try:
        return await device_gateway.submit_action(job_id, raw)
    except DeviceGatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/device/job/{job_id}/result")
async def api_device_job_result(
    job_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
):
    """The planner's final answer."""
    _require_device_api(authorization)
    try:
        return await device_gateway.finish_job(
            job_id,
            status=str(payload.get("status") or "completed"),
            message=str(payload.get("message") or ""),
        )
    except DeviceGatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/device/command")
async def api_device_command(
    req: CommandRequest,
    authorization: str | None = Header(default=None),
):
    """Run one phone task and wait for the vision loop to finish.

    The endpoint is intentionally synchronous from the caller's point of view:
    Telegram sends one request and gets a final message.  The phone itself sends
    observations/actions asynchronously over ``/ws/device``.

    With OYI_SANDBOX_PLANNER=1 the decision loop runs inside a Nebius
    Sandbox and this process only relays frames.
    """
    _require_device_api(authorization)
    if sandbox_planner_enabled():
        return await _run_command_in_sandbox(req)
    try:
        result = await device_gateway.execute_command(
            text=req.text,
            device_id=req.device_id,
            max_steps=req.max_steps,
            timeout_s=req.timeout_s,
            thread_id=req.thread_id,
        )
    except (DeviceGatewayError, DeviceOfflineError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result.model_dump()


async def _run_command_in_sandbox(req: CommandRequest) -> dict:
    """Drive the phone from a Nebius Sandbox instead of from this process."""
    public_url = os.getenv("OYI_PUBLIC_URL", "").strip().rstrip("/")
    if not public_url:
        raise HTTPException(
            status_code=500,
            detail="OYI_PUBLIC_URL must be set so the sandbox can reach the gateway.",
        )

    job = await device_gateway.create_job(
        device_id=req.device_id,
        instruction=req.text,
        max_steps=req.max_steps,
    )
    LOGGER.info("running command %s in a Nebius Sandbox", job.id)

    planner_task = asyncio.create_task(
        run_sandbox_planner(
            gateway_url=public_url,
            gateway_token=_device_api_token(),
            job_id=job.id,
            model=os.getenv("OYI_DEVICE_VISION_MODEL", "nvidia/Nemotron-3.5-Super-VL"),
            max_steps=req.max_steps,
            timeout_s=req.timeout_s + 120.0,
        )
    )
    result_task = asyncio.create_task(
        device_gateway.job_result(job.id, timeout=req.timeout_s + 60.0)
    )
    try:
        # Wait for whichever finishes first. If the planner process dies before
        # the phone job finishes, finish the job with the real error instead of
        # leaving the request to wait out the full timeout.
        done, _ = await asyncio.wait(
            {planner_task, result_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if result_task in done:
            return result_task.result().model_dump()

        if planner_task.cancelled():
            detail = "Sandbox planner was cancelled."
        else:
            error = planner_task.exception()
            if error is not None:
                LOGGER.error(
                    "sandbox planner for job %s failed", job.id, exc_info=error
                )
                detail = f"Sandbox planner failed: {error}"
            else:
                info = planner_task.result() or {}
                output = (info.get("stderr") or info.get("stdout") or "").strip()
                LOGGER.error(
                    "sandbox planner for job %s exited without a result: exit=%s %s",
                    job.id,
                    info.get("exit_code"),
                    output[-1000:],
                )
                detail = f"Sandbox planner exited without a result (exit {info.get('exit_code')})."
                if output:
                    detail = f"{detail} {output[-500:]}"

        await device_gateway.finish_job(job.id, status="error", message=detail)
        return (await result_task).model_dump()
    finally:
        planner_task.cancel()
        result_task.cancel()


@app.post("/api/device/cancel/{command_id}")
async def api_device_cancel(
    command_id: str,
    authorization: str | None = Header(default=None),
):
    _require_device_api(authorization)
    return {"cancelled": await device_gateway.cancel(command_id, reason="Cancelled by user")}


@app.websocket("/ws/device")
async def device_ws(websocket: WebSocket):
    """Persistent outbound WebSocket from the paired Android app.

    The phone connects out, so OYI never needs an inbound port or ADB on the
    device.  The first frame must be a ``hello`` with a stable device id.
    """
    await websocket.accept()
    if not _device_ws_token_ok(websocket):
        await websocket.close(code=1008, reason="Unauthorized device")
        return

    connection: DeviceConnection | None = None
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
        try:
            hello = DeviceHello.model_validate_json(first)
        except Exception:  # noqa: BLE001 - reject malformed handshakes
            await websocket.close(code=1008, reason="Expected a valid hello frame")
            return

        connection = DeviceConnection(websocket=websocket, hello=hello)
        await device_gateway.register(connection)
        await connection.send(
            {
                "type": "hello_ack",
                "device_id": hello.device_id,
                "server_time": time.time(),
                "model": os.getenv("OYI_DEVICE_VISION_MODEL", "nvidia/Nemotron-3.5-Super-VL"),
            }
        )
        LOGGER.info("Device WebSocket ready id=%s", hello.device_id)

        while True:
            message = await websocket.receive_text()
            await device_gateway.handle_message(connection, message)
    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        await websocket.close(code=1008, reason="Timed out waiting for hello")
    except Exception:
        LOGGER.exception("Device WebSocket failed")
    finally:
        if connection is not None:
            await device_gateway.unregister(connection)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - already closed
            pass


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """Run text prompt through OYI agent.

    The async path is required: tools such as `control_phone` are coroutines,
    and the synchronous agent entrypoint cannot invoke them.
    """
    if not req.prompt.strip():
        return {"response": ""}
    try:
        chunks: list[str] = []
        async for chunk in agent_service.astream(req.prompt, req.thread_id):
            chunks.append(chunk)
        reply = "".join(chunks)
        if req.plain:
            # Strip once on the joined text: a tag can be split across chunks.
            reply = strip_telegram_html(reply)
        return {"response": reply}
    except Exception as exc:
        LOGGER.exception("Agent chat failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/chat/stream")
async def api_chat_stream(req: ChatRequest):
    """Stream the agent's reply as newline-delimited JSON.

    Each line is {"text": "..."} so a client can render tokens as they
    arrive.  Async tools (phone control) work on this path too.
    """
    if not req.prompt.strip():
        return StreamingResponse(iter(()), media_type="application/x-ndjson")

    async def generate():
        plain = PlainTextStream() if req.plain else None
        try:
            async for chunk in agent_service.astream(req.prompt, req.thread_id):
                text = plain.feed(chunk) if plain is not None else chunk
                if text:
                    yield json.dumps({"text": text}) + "\n"
            if plain is not None:
                tail = plain.flush()
                if tail:
                    yield json.dumps({"text": tail}) + "\n"
        except Exception as exc:  # noqa: BLE001 - reported to the client
            LOGGER.exception("Agent chat stream failed")
            yield json.dumps({"error": str(exc)}) + "\n"
        yield json.dumps({"done": True}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


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

