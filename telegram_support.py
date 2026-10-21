"""Reusable Telegram settings, throttling, and message formatting helpers."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Final

from config import format_agent_response  # noqa: F401  (kept: plain-text path for fallbacks)
from telegram_format import (
    markdown_to_telegram_html,
    strip_telegram_html,
    telegram_preview,
)
from telegram_format import split_telegram_text as _split_telegram_html

TELEGRAM_TEXT_LIMIT: Final[int] = 4096
TELEGRAM_EDIT_MARGIN: Final[int] = 96


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_user_id: int
    edit_interval_seconds: float = 1.0
    min_edit_delta_chars: int = 24
    max_prompt_chars: int = 45_000
    max_document_bytes: int = 12 * 1024 * 1024
    requests_per_minute: int = 12
    burst_requests: int = 4
    max_concurrent_requests: int = 2
    polling_allowed_updates: tuple[str, ...] = ("message",)

    def __post_init__(self) -> None:
        positive = {
            "edit_interval_seconds": self.edit_interval_seconds,
            "min_edit_delta_chars": self.min_edit_delta_chars,
            "max_prompt_chars": self.max_prompt_chars,
            "max_document_bytes": self.max_document_bytes,
            "requests_per_minute": self.requests_per_minute,
            "burst_requests": self.burst_requests,
            "max_concurrent_requests": self.max_concurrent_requests,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")

    @classmethod
    def from_env(cls) -> "Settings":
        token = require_env("TELEGRAM_BOT_TOKEN")
        allowed_user_raw = require_env("My_Telegram_ID")
        try:
            allowed_user_id = int(allowed_user_raw)
        except ValueError as exc:
            raise RuntimeError("My_Telegram_ID must be an integer Telegram user ID.") from exc
        return cls(
            telegram_bot_token=token,
            allowed_user_id=allowed_user_id,
            edit_interval_seconds=get_float_env("TELEGRAM_EDIT_INTERVAL_SECONDS", 1.0),
            min_edit_delta_chars=get_int_env("TELEGRAM_MIN_EDIT_DELTA_CHARS", 24),
            max_prompt_chars=get_int_env("TELEGRAM_MAX_PROMPT_CHARS", 45_000),
            max_document_bytes=get_int_env("TELEGRAM_MAX_DOCUMENT_BYTES", 12 * 1024 * 1024),
            requests_per_minute=get_int_env("TELEGRAM_REQUESTS_PER_MINUTE", 12),
            burst_requests=get_int_env("TELEGRAM_BURST_REQUESTS", 4),
            max_concurrent_requests=get_int_env("TELEGRAM_MAX_CONCURRENT_REQUESTS", 2),
        )


class TokenBucketRateLimiter:
    """Simple async token bucket keyed by Telegram user ID."""

    def __init__(self, rate_per_minute: int, burst: int) -> None:
        if rate_per_minute <= 0 or burst <= 0:
            raise ValueError("rate_per_minute and burst must be greater than zero")
        self._capacity = float(burst)
        self._refill_per_second = rate_per_minute / 60
        self._buckets: dict[int, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, user_id: int) -> tuple[bool, float]:
        async with self._lock:
            now = time.monotonic()
            tokens, updated_at = self._buckets.get(user_id, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - updated_at) * self._refill_per_second)
            if tokens >= 1:
                self._buckets[user_id] = (tokens - 1, now)
                return True, 0
            retry_after = (1 - tokens) / self._refill_per_second
            self._buckets[user_id] = (tokens, now)
            return False, retry_after


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc


def get_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number.") from exc


def clamp_prompt(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


# Agent replies are formatted as Telegram HTML (parse_mode=HTML): the LLM's
# markdown is converted to supported tags (<b>, <i>, <code>, <pre>, <a>, ...),
# truncated safely and split into <=4096-char chunks. See telegram_format.py.


def split_telegram_text(text: str) -> list[str]:
    """Model response chunks as Telegram HTML (safe<=4096 chars each)."""
    return _split_telegram_html(text)
