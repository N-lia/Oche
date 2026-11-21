"""Messaging adapters for OYI."""

from channels.base import ChannelAdapter, InboundMessage, OutboundMessage
from channels.registry import ChannelRegistry
from channels.telegram import TelegramChannel

__all__ = [
    "ChannelAdapter",
    "ChannelRegistry",
    "InboundMessage",
    "OutboundMessage",
    "TelegramChannel",
]
