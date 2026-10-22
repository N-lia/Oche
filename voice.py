"""Chatterbox Turbo voice synthesis and proactive Telegram voice delivery for OYI."""

from __future__ import annotations

import asyncio
import io
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from memory import _CURRENT_USER_ID

LOGGER = logging.getLogger("oyi.voice")

TelegramVoiceSender = Callable[[int | str, bytes, str], Awaitable[None]]
_TELEGRAM_VOICE_SENDER: TelegramVoiceSender | None = None
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def get_chatterbox_url() -> str:
    """Read Chatterbox Turbo Modal URL from environment with fallback/trimming."""
    url = os.environ.get("CHATTERBOX_MODAL_URL") or os.environ.get("CHATTERBOX_MOODAL_URL") or ""
    return url.strip()


def get_tts_provider() -> str:
    """Which TTS backend ``synthesize_speech`` should call (chatterbox|cosyvoice|phonon)."""
    return (os.environ.get("TTS_PROVIDER") or "chatterbox").strip().lower()


def get_cosyvoice_url() -> str:
    """Read the CosyVoice 2 Modal URL from the environment."""
    return (os.environ.get("COSYVOICE_MODAL_URL") or "").strip()


def get_tts_url() -> str:
    """Resolve the active TTS endpoint from ``TTS_PROVIDER``."""
    if get_tts_provider() == "cosyvoice":
        return get_cosyvoice_url()
    return get_chatterbox_url()


def register_telegram_voice_sender(sender: TelegramVoiceSender) -> None:
    """Register the proactive Telegram voice delivery callback from TG.py."""
    global _TELEGRAM_VOICE_SENDER, _MAIN_LOOP
    _TELEGRAM_VOICE_SENDER = sender
    try:
        _MAIN_LOOP = asyncio.get_running_loop()
    except RuntimeError:
        pass


class VoiceNoteInput(BaseModel):
    text: str = Field(
        ...,
        description="The text to speak aloud in the voice note.",
    )
    mood: str = Field(
        default="normal",
        description="Optional voice emotion: 'normal' (calm/chat), 'cheerful' (happy/celebratory), or 'angry' (stern/firm).",
    )
    event: str = Field(
        default="chat",
        description="Optional context event: 'chat' (normal conversation), 'task_done' (cheerful completion), or 'slop' (stern warning).",
    )
    caption: str = Field(
        default="",
        description="Optional brief text caption to accompany the voice message bubble in Telegram.",
    )


_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return a shared persistent AsyncClient with connection pooling."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=90.0,
            limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=120),
        )
    return _HTTP_CLIENT


async def synthesize_speech(
    text: str,
    mood: str = "normal",
    event: str = "chat",
    **dials,
) -> bytes:
    """Call the configured backend to synthesize speech.

    Phonon runs in-process on the VM (no HTTP, WAV out). Chatterbox Turbo is
    the default remote option; set ``TTS_PROVIDER=cosyvoice`` (and
    ``COSYVOICE_MODAL_URL``) to compare against CosyVoice 2 instead.
    """
    provider = get_tts_provider()
    if provider == "phonon":
        from phonon_tts import synthesize_phonon_wav

        return await synthesize_phonon_wav(text, mood=mood, event=event, **dials)
    url = get_tts_url()
    if not url:
        env_name = "COSYVOICE_MODAL_URL" if provider == "cosyvoice" else "CHATTERBOX_MODAL_URL"
        raise ValueError(f"{env_name} is not configured in .env (TTS_PROVIDER={provider})")

    # Ensure URL ends without trailing slash
    target_url = url.rstrip("/")
    LOGGER.info(
        "Calling %s TTS at %s (text_len=%d, mood=%s, event=%s, dials=%s)",
        provider,
        target_url,
        len(text),
        mood,
        event,
        dials,
    )

    client = _get_http_client()
    payload = {
        "text": text,
        "mood": mood,
        "event": event,
        "format": "mp3",
    }
    # Include any custom dial overrides (exaggeration, cfg_weight, temperature, etc.)
    for k, v in dials.items():
        if v is not None:
            payload[k] = v

    response = await client.post(target_url, json=payload)
    if response.status_code != 200:
        LOGGER.error("%s TTS failed with status %d: %s", provider, response.status_code, response.text)
        raise RuntimeError(f"{provider} TTS returned status {response.status_code}: {response.text}")

    content = response.content
    if not content:
        raise RuntimeError(f"{provider} TTS returned empty audio payload")

    return content


@tool("send_voice_note", args_schema=VoiceNoteInput)
async def send_voice_note(text: str, mood: str = "normal", event: str = "chat", caption: str = "") -> str:
    """Synthesize text into a voice note and deliver it as a voice bubble to Sir Mario in Telegram.

    CRITICAL TRIGGER RESTRICTION:
    - ONLY call this tool when Sir Mario EXPLICITLY asks for a voice note, voice message, audio message, or spoken audio.
    - Examples of explicit user triggers: "send a voice note", "send me a voice message", "speak to me", "reply in voice", "say this in audio".
    - NEVER call this tool for normal text conversations, greetings, general questions, explanations, coding, or chit-chat.
    - Standard conversation MUST ALWAYS be answered in text.
    """
    chat_id = _CURRENT_USER_ID.get()
    if not chat_id:
        chat_id = os.environ.get("ALLOWED_USER_ID", "")

    if not chat_id:
        return "Failed to send voice note: user chat_id is unknown."

    try:
        audio_bytes = await synthesize_speech(text, mood=mood, event=event)

        if not _TELEGRAM_VOICE_SENDER:
            return "Voice synthesis succeeded, but Telegram voice sender is not registered."

        # Schedule delivery on the main Telegram event loop
        target_loop = _MAIN_LOOP
        if target_loop and target_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                _TELEGRAM_VOICE_SENDER(chat_id, audio_bytes, caption),
                target_loop,
            )
            # Await the delivery with a short timeout
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=15.0)
        else:
            await _TELEGRAM_VOICE_SENDER(chat_id, audio_bytes, caption)

        LOGGER.info("Voice note successfully sent to chat_id=%s (size=%d bytes)", chat_id, len(audio_bytes))
        return "Voice note successfully synthesized and delivered to Sir Mario in Telegram."

    except Exception as exc:
        LOGGER.exception("Failed to synthesize or send voice note")
        return f"Error sending voice note: {exc}"


VOICE_TOOLS: list[BaseTool] = [send_voice_note]
