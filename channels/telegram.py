"""Telegram adapter for the normalized channel boundary."""

from __future__ import annotations

import logging
from typing import Any

from telegram.constants import ParseMode
from telegram.error import BadRequest

from channels.base import OutboundMessage
from telegram_support import split_telegram_text, strip_telegram_html

LOGGER = logging.getLogger("oyi.channels.telegram")


class TelegramChannel:
    name = "telegram"

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    async def send(self, message: OutboundMessage) -> None:
        chat_id: int | str = message.recipient
        if str(chat_id).lstrip("-").isdigit():
            chat_id = int(chat_id)

        for chunk in split_telegram_text(message.text):
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                )
            except BadRequest as exc:
                lowered = str(exc).lower()
                if "parse" not in lowered and "entities" not in lowered and "unsupported" not in lowered:
                    raise
                LOGGER.warning("Telegram rejected HTML; sending plain text")
                await self.bot.send_message(chat_id=chat_id, text=strip_telegram_html(chunk))
