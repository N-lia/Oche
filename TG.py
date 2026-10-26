from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

import httpx
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from asr_client import atranscribe
from channels import ChannelRegistry, TelegramChannel
from main import astream_chat_agent
from memory import reconcile_preferences
from subagent import register_telegram_notifier
from voice import register_telegram_voice_sender
from telegram_support import (
    Settings,
    TokenBucketRateLimiter,
    clamp_prompt,
    split_telegram_text,
    telegram_preview,
    strip_telegram_html,
)

load_dotenv()

LOGGER = logging.getLogger("oyi.telegram")

SUPPORTED_EXTENSIONS: Final[set[str]] = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".txt"}
SUPPORTED_MIME_TYPES: Final[set[str]] = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}


def setup_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=getattr(logging, log_level, logging.INFO),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def get_rate_limiter(context: ContextTypes.DEFAULT_TYPE) -> TokenBucketRateLimiter:
    return context.application.bot_data["rate_limiter"]


def get_request_semaphore(context: ContextTypes.DEFAULT_TYPE) -> asyncio.Semaphore:
    return context.application.bot_data["request_semaphore"]


def conversation_thread_id(chat_id: int) -> str:
    return str(chat_id)


def is_allowed(update: Update, settings: Settings) -> bool:
    return update.effective_user is not None and update.effective_user.id == settings.allowed_user_id


async def reject_unauthorized(update: Update) -> None:
    if update.effective_message is not None:
        await update.effective_message.reply_text("You are not allowed to use this bot.")


async def enforce_access_and_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = get_settings(context)
    message = update.effective_message
    if not is_allowed(update, settings):
        user_id = update.effective_user.id if update.effective_user else None
        LOGGER.warning("Unauthorized Telegram access attempt user_id=%s", user_id)
        await reject_unauthorized(update)
        return False

    if message is None:
        LOGGER.warning("Allowed update had no effective message")
        return False

    limiter = get_rate_limiter(context)
    allowed, retry_after = await limiter.allow(settings.allowed_user_id)
    if not allowed:
        await message.reply_text(f"Rate limit reached. Please try again in {retry_after:.0f} seconds.")
        return False

    return True


def extract_document_text(data: bytes, filename: str) -> str:
    """Extract text from a document using LiteParse."""
    from liteparse import LiteParse

    parser = LiteParse()
    suffix = Path(filename).suffix or ".pdf"
    tmp_path = ""

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        result = parser.parse(tmp_path)
        return getattr(result, "text", str(result)).strip()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


async def safe_edit_text(message, text: str, parse_mode: str | None = ParseMode.HTML) -> bool:
    """Edit a message, preferring Telegram HTML and falling back to plain text.

    Formatting failures (``Can't parse entities`` / ``Unsupported start tag``)
    are retried once without a parse mode, so a bad reply can never wedge the
    streaming updater.
    """
    try:
        await message.edit_text(text, parse_mode=parse_mode)
        return True
    except BadRequest as exc:
        lowered = str(exc).lower()
        if "message is not modified" in lowered:
            return True
        if parse_mode and ("parse" in lowered or "entities" in lowered or "unsupported" in lowered or "tag" in lowered):
            LOGGER.warning("Telegram rejected HTML edit, falling back to plain text: %s", exc)
            try:
                await message.edit_text(strip_telegram_html(text))
                return True
            except TelegramError:
                LOGGER.warning("Plain-fallback edit also rejected", exc_info=True)
        elif "message is not modified" not in lowered:
            LOGGER.warning("Telegram rejected message edit: %s", exc)
    except RetryAfter as exc:
        LOGGER.info("Telegram asked to retry edit after %s seconds", exc.retry_after)
        await asyncio.sleep(float(exc.retry_after))
        return await safe_edit_text(message, text, parse_mode=parse_mode)
    except (NetworkError, TimedOut) as exc:
        LOGGER.warning("Transient Telegram edit error: %s", exc)
    except TelegramError:
        LOGGER.exception("Unexpected Telegram edit error")
    return False


async def safe_send_html(message, text: str, parse_mode: str | None = ParseMode.HTML) -> bool:
    """Send a new message preferring HTML parse mode, with plain-text fallback."""
    try:
        await message.reply_text(text, parse_mode=parse_mode)
        return True
    except BadRequest as exc:
        lowered = str(exc).lower()
        if parse_mode and ("parse" in lowered or "entities" in lowered or "unsupported" in lowered or "tag" in lowered):
            LOGGER.warning("Telegram rejected HTML send, falling back to plain text: %s", exc)
            try:
                await message.reply_text(strip_telegram_html(text))
                return True
            except TelegramError:
                LOGGER.warning("Plain-fallback send also rejected", exc_info=True)
        else:
            LOGGER.warning("Telegram rejected message send: %s", exc)
    except RetryAfter as exc:
        LOGGER.info("Telegram asked to retry send after %s seconds", exc.retry_after)
        await asyncio.sleep(float(exc.retry_after))
        return await safe_send_html(message, text, parse_mode=parse_mode)
    except (NetworkError, TimedOut) as exc:
        LOGGER.warning("Transient Telegram send error: %s", exc)
    except TelegramError:
        LOGGER.exception("Unexpected Telegram send error")
    return False


async def safe_send_chat_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int, action: str) -> None:
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=action)
    except (NetworkError, TimedOut) as exc:
        LOGGER.info("Skipping chat action after transient Telegram error: %s", exc)
    except RetryAfter as exc:
        LOGGER.info("Skipping chat action after Telegram retry-after=%s", exc.retry_after)
    except TelegramError:
        LOGGER.warning("Skipping chat action after Telegram error", exc_info=True)


async def stream_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str,
    thread_id_str: str,
) -> None:
    settings = get_settings(context)
    message = update.effective_message
    chat = update.effective_chat

    if message is None or chat is None:
        LOGGER.warning("Received update without effective message or chat")
        return

    await safe_send_chat_action(context, chat.id, ChatAction.TYPING)
    sent_message = await message.reply_text("Thinking...")
    accumulated = ""
    last_edit_text = ""
    last_edit_time = time.monotonic()

    async with get_request_semaphore(context):
        try:
            async for chunk in astream_chat_agent(user_input, thread_id=thread_id_str):
                if not chunk:
                    continue

                accumulated += chunk
                now = time.monotonic()

                # Eager first edit: show the very first words immediately (>= 6 chars)
                # Subsequent edits: throttled by edit_interval_seconds to stay well within Telegram limits
                if not last_edit_text:
                    edit_due = len(accumulated.strip()) >= 6
                    content_changed = True
                else:
                    edit_due = now - last_edit_time >= settings.edit_interval_seconds
                    content_changed = len(accumulated) - len(last_edit_text) >= settings.min_edit_delta_chars

                if edit_due and content_changed:
                    preview = telegram_preview(accumulated)
                    if preview != last_edit_text and await safe_edit_text(sent_message, preview):
                        last_edit_text = preview
                        last_edit_time = now

            final_text = accumulated.strip() or "I could not produce a response."
            final_chunks = split_telegram_text(final_text)
            if final_chunks[0] != last_edit_text:
                await safe_edit_text(sent_message, final_chunks[0])
            for chunk in final_chunks[1:]:
                await safe_send_html(message, chunk)
        except Exception:
            LOGGER.exception("Agent streaming failed")
            await safe_edit_text(sent_message, "Sorry, something went wrong while generating a response.")


def get_webapp_url() -> str:
    return os.getenv("TELEGRAM_WEBAPP_URL", "").strip()


def get_device_gateway_url() -> str:
    return os.getenv("OYI_DEVICE_GATEWAY_URL", "").strip().rstrip("/")


def get_device_api_token() -> str:
    return (
        os.getenv("OYI_DEVICE_API_TOKEN", "").strip()
        or os.getenv("OYI_DEVICE_TOKEN", "").strip()
    )


async def device_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a natural-language task to the paired Android phone."""
    if not await enforce_access_and_rate_limit(update, context):
        return

    message = update.effective_message
    if message is None:
        return

    text = " ".join(context.args).strip()
    if not text:
        await message.reply_text(
            "Usage: <code>/device open Settings and turn on dark mode</code>\n"
            "The phone must have the OYI app running and be paired.",
            parse_mode=ParseMode.HTML,
        )
        return

    base_url = get_device_gateway_url()
    token = get_device_api_token()
    if not base_url or not token:
        await message.reply_text(
            "Phone control is not configured. Set <code>OYI_DEVICE_GATEWAY_URL</code> "
            "and <code>OYI_DEVICE_API_TOKEN</code> in the bot environment.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_message = await message.reply_text("📱 Sending to your phone...")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=240.0, write=20.0, pool=10.0)
        ) as client:
            response = await client.post(
                f"{base_url}/api/device/command",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "text": text,
                    "max_steps": 12,
                    "timeout_s": 220,
                    "thread_id": conversation_thread_id(update.effective_chat.id),
                },
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        await safe_edit_text(
            status_message,
            f"📱 Phone request failed ({exc.response.status_code}): "
            f"{html.escape(exc.response.text[:400])}",
        )
        return
    except Exception as exc:  # noqa: BLE001 - reported to the Telegram user
        LOGGER.exception("Phone command request failed")
        await safe_edit_text(status_message, f"📱 Phone request failed: {html.escape(str(exc))}")
        return

    status = html.escape(str(data.get("status", "error")))
    body = html.escape(str(data.get("message") or data.get("error") or "No result from the phone."))
    steps = data.get("steps", 0)
    await safe_edit_text(
        status_message,
        f"📱 <b>Phone {status}</b> ({steps} step{'s' if steps != 1 else ''})\n{body}",
    )


async def device_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List paired Android phones and their connection state."""
    if not await enforce_access_and_rate_limit(update, context):
        return

    message = update.effective_message
    if message is None:
        return

    base_url = get_device_gateway_url()
    token = get_device_api_token()
    if not base_url or not token:
        await message.reply_text("Phone control is not configured.")
        return

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{base_url}/api/device/devices",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            devices = response.json().get("devices", [])
    except Exception as exc:  # noqa: BLE001 - reported to the Telegram user
        LOGGER.exception("Could not list devices")
        await message.reply_text(f"Could not list phones: {html.escape(str(exc))}")
        return

    if not devices:
        await message.reply_text("No phones are connected. Open the OYI Android app and start the overlay.")
        return

    lines = ["📱 <b>Paired phones</b>"]
    for device in devices:
        state = "online" if device.get("connected") else "offline"
        name = html.escape(str(device.get("name") or device.get("model") or "Android"))
        device_id = html.escape(str(device.get("device_id", "")))
        lines.append(f"• <b>{name}</b> — {state}\n  <code>{device_id}</code>")
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def live_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the real-time OYI Live WebApp (voice + text)."""
    if not await enforce_access_and_rate_limit(update, context):
        return

    webapp_url = get_webapp_url()
    if not webapp_url:
        await update.effective_message.reply_text(
            "⚠️ The Live Chat WebApp URL is not configured yet.\n\n"
            "Set `TELEGRAM_WEBAPP_URL` in the bot `.env` to enable /livechat."
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎧 Open OYI Live", web_app=WebAppInfo(url=webapp_url))]
    ])
    await update.effective_message.reply_text(
        "*🎧 OYI Live* — real-time two-way voice.\n\n"
        "Tap to open the WebApp. Talk naturally; I backchannel while you speak, "
        "and you can cut me off mid-sentence — I'll stop and listen.",
        reply_markup=keyboard,
    )


async def voice_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for /live-chat (kept for backwards compatibility)."""
    await live_chat(update, context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await enforce_access_and_rate_limit(update, context):
        return
    webapp_url = get_webapp_url()
    if webapp_url:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎧 Open OYI Live", web_app=WebAppInfo(url=webapp_url))]
        ])
        await update.effective_message.reply_text(
            "Hello Sir Mario! I am OYI, your personal AI assistant.\n\n"
            "Chat with me here, or tap below for the real-time live voice interface "
            "(or send /livechat).",
            reply_markup=keyboard,
        )
    else:
        await update.effective_message.reply_text(
            "Hello! I am OYI, your personal AI assistant. How can I help you today?"
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await enforce_access_and_rate_limit(update, context):
        return

    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return

    settings = get_settings(context)
    user_input, truncated = clamp_prompt(message.text.strip(), settings.max_prompt_chars)
    if truncated:
        await message.reply_text("Your message was long, so I processed the first allowed portion.")

    await stream_reply(update, context, user_input, conversation_thread_id(chat.id))


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await enforce_access_and_rate_limit(update, context):
        return

    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    media = message.voice or message.audio
    if media is None:
        return

    await safe_send_chat_action(context, chat.id, ChatAction.RECORD_VOICE)
    status_msg = await message.reply_text("🎙️ Transcribing voice note...")

    try:
        media_file = await context.bot.get_file(media.file_id)
        voice_bytes = bytes(await media_file.download_as_bytearray())

        result = await atranscribe(voice_bytes)
        if result.error:
            await safe_edit_text(status_msg, "⚠️ Parakeet ASR error: " + html.escape(str(result.error)))
            return

        user_text = result.text.strip()
        eou = result.eou_detected

        if not user_text:
            await safe_edit_text(status_msg, "⚠️ Could not transcribe any speech from this voice note.")
            return

        eou_tag = " [EOU detected]" if eou else ""
        await safe_edit_text(status_msg, f"🎙️ <i>\"{html.escape(user_text)}\"</i>{eou_tag}")

        # Stream agent reply to the transcribed text
        await stream_reply(update, context, user_text, conversation_thread_id(chat.id))

    except Exception as exc:
        LOGGER.exception("Voice handling failed")
        await safe_edit_text(status_msg, "⚠️ Failed to process voice note: " + html.escape(str(exc)))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await enforce_access_and_rate_limit(update, context):
        return

    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or message.document is None:
        return

    settings = get_settings(context)
    doc = message.document
    filename = doc.file_name or "document"
    ext = Path(filename).suffix.lower()
    mime_type = doc.mime_type or ""

    if ext not in SUPPORTED_EXTENSIONS and mime_type not in SUPPORTED_MIME_TYPES:
        await message.reply_text("Unsupported file type. Please send a PDF, DOCX, DOC, PPTX, XLSX, or TXT file.")
        return

    if doc.file_size and doc.file_size > settings.max_document_bytes:
        max_mb = settings.max_document_bytes / (1024 * 1024)
        await message.reply_text(f"That document is too large. The current limit is {max_mb:.0f} MB.")
        return

    status_message = await message.reply_text(f"Reading {filename}...")

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
        if len(file_bytes) > settings.max_document_bytes:
            max_mb = settings.max_document_bytes / (1024 * 1024)
            await safe_edit_text(status_message, f"That document is too large. The current limit is {max_mb:.0f} MB.")
            return

        text = extract_document_text(file_bytes, filename)
    except Exception:
        LOGGER.exception("Document extraction failed filename=%s mime_type=%s", filename, mime_type)
        await safe_edit_text(status_message, "Could not read that document.")
        return

    if not text:
        await safe_edit_text(status_message, "The document appears to have no extractable text.")
        return

    caption = (message.caption or "").strip()
    question = caption or "Please summarise this document."
    prompt = f"{question}\n\n---\nDocument ({filename}) content:\n{text}"
    prompt, truncated = clamp_prompt(prompt, settings.max_prompt_chars)

    if truncated:
        await safe_edit_text(status_message, "Document text was long, so I processed the first allowed portion.")
    else:
        await safe_edit_text(status_message, "Document read. Generating response...")

    await stream_reply(update, context, prompt, conversation_thread_id(chat.id))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Unhandled Telegram application error update=%s", update, exc_info=context.error)


async def _nightly_reconciliation_loop(application: Application) -> None:
    settings = application.bot_data["settings"]
    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        await asyncio.to_thread(reconcile_preferences, str(settings.allowed_user_id))


async def _on_post_init(application: Application) -> None:
    async def deliver_subagent_message(chat_id: int | str, task_name: str, result: str) -> None:
        header = f"🔔 <b>Subagent Task Completed: {html.escape(task_name)}</b>\n\n"
        full_text = header + result
        chunks = split_telegram_text(full_text)
        target_chat_id = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        for chunk in chunks:
            try:
                sent = await application.bot.send_message(chat_id=target_chat_id, text=chunk, parse_mode=ParseMode.HTML)
            except BadRequest as exc:
                if "parse" in str(exc).lower() or "entities" in str(exc).lower() or "unsupported" in str(exc).lower():
                    sent = await application.bot.send_message(chat_id=target_chat_id, text=strip_telegram_html(chunk))
                else:
                    LOGGER.exception("Failed to deliver subagent result chunk to chat_id=%s", chat_id)
                    continue
            except Exception:
                LOGGER.exception("Failed to deliver subagent result chunk to chat_id=%s", chat_id)
                continue
            LOGGER.debug("Delivered subagent chunk %s to chat_id=%s", getattr(sent, "message_id", "?"), target_chat_id)

    register_telegram_notifier(deliver_subagent_message)

    async def deliver_voice_message(chat_id: int | str, audio_bytes: bytes, caption: str = "") -> None:
        target_chat_id = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        try:
            await application.bot.send_voice(
                chat_id=target_chat_id,
                voice=io.BytesIO(audio_bytes),
                caption=caption or None,
            )
        except Exception:
            LOGGER.exception("Failed to deliver voice note to chat_id=%s", chat_id)
            raise

    register_telegram_voice_sender(deliver_voice_message)

    channels = application.bot_data.get("channels")
    if channels is None:
        channels = ChannelRegistry()
        application.bot_data["channels"] = channels
        channels.register(TelegramChannel(application.bot))

    webapp_url = get_webapp_url()
    if webapp_url:
        try:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🎧 Live Chat", web_app=WebAppInfo(url=webapp_url))
            )
            LOGGER.info("Configured Telegram Bot Menu Button for OYI Live: %s", webapp_url)
        except Exception:
            LOGGER.exception("Failed to set chat menu button for WebApp")

    try:
        await application.bot.set_my_commands(
            [
                BotCommand("livechat", "🎧 Real-time voice chat (WebApp)"),
                BotCommand("voice", "🎧 Real-time voice chat (WebApp)"),
                BotCommand("device", "📱 Control my paired Android phone"),
                BotCommand("devices", "📱 List paired Android phones"),
                BotCommand("start", "Show the welcome message"),
            ]
        )
    except Exception:
        LOGGER.exception("Failed to set bot commands")

    application.bot_data["reconciliation_task"] = application.create_task(
        _nightly_reconciliation_loop(application),
        name="oyi-nightly-preference-reconciliation",
    )


async def _stop_nightly_reconciliation(application: Application) -> None:
    task = application.bot_data.get("reconciliation_task")
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def build_application(settings: Settings) -> Application:
    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_on_post_init)
        .post_shutdown(_stop_nightly_reconciliation)
        .build()
    )
    app.bot_data["settings"] = settings
    app.bot_data["rate_limiter"] = TokenBucketRateLimiter(
        rate_per_minute=settings.requests_per_minute,
        burst=settings.burst_requests,
    )
    app.bot_data["request_semaphore"] = asyncio.Semaphore(settings.max_concurrent_requests)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("livechat", live_chat))
    app.add_handler(CommandHandler("voice", voice_app))
    app.add_handler(CommandHandler("device", device_command))
    app.add_handler(CommandHandler("devices", device_list))
    # Telegram only permits [a-z0-9_] in command names; also accept a typed
    # "/live-chat" so the natural spelling works.
    app.add_handler(MessageHandler(filters.Regex(r"^/live-?chat(?:@\w+)?\b"), live_chat))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    setup_logging()
    settings = Settings.from_env()
    app = build_application(settings)
    LOGGER.info("Telegram bot is running")
    app.run_polling(allowed_updates=settings.polling_allowed_updates)


if __name__ == "__main__":
    main()
