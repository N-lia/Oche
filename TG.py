from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from main import astream_chat_agent
from memory import reconcile_preferences
from telegram_support import (
    Settings,
    TokenBucketRateLimiter,
    clamp_prompt,
    split_telegram_text,
    telegram_preview,
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


async def safe_edit_text(message, text: str) -> bool:
    try:
        await message.edit_text(telegram_preview(text))
        return True
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            LOGGER.warning("Telegram rejected message edit: %s", exc)
    except RetryAfter as exc:
        LOGGER.info("Telegram asked to retry edit after %s seconds", exc.retry_after)
        await asyncio.sleep(float(exc.retry_after))
    except (NetworkError, TimedOut) as exc:
        LOGGER.warning("Transient Telegram edit error: %s", exc)
    except TelegramError:
        LOGGER.exception("Unexpected Telegram edit error")
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
                await message.reply_text(chunk)
        except Exception:
            LOGGER.exception("Agent streaming failed")
            await safe_edit_text(sent_message, "Sorry, something went wrong while generating a response.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await enforce_access_and_rate_limit(update, context):
        return
    await update.effective_message.reply_text("Hello! I am OYI, your personal AI assistant. How can I help you today?")


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


async def _start_nightly_reconciliation(application: Application) -> None:
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
        .post_init(_start_nightly_reconciliation)
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
