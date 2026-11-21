"""Messaging-channel boundary for OYI.

A channel is a transport for conversation (Telegram, WhatsApp, Discord, SMS),
not a phone capability.  Device control lives in ``device_gateway`` and can be
triggered from any channel through the shared agent/tool layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    sender_id: str
    text: str = ""
    thread_id: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None


@dataclass(frozen=True)
class OutboundMessage:
    recipient: str
    text: str
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ChannelAdapter(Protocol):
    name: str

    async def send(self, message: OutboundMessage) -> None:
        """Deliver one normalized message to the channel."""
