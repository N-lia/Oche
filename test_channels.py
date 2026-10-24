#!/usr/bin/env python3
"""Tests for the messaging-channel boundary.

Run:
    .venv/bin/python test_channels.py
"""

from __future__ import annotations

from channels.base import ChannelAdapter, InboundMessage, OutboundMessage
from channels.registry import ChannelRegistry


class EchoChannel:
    name = "echo"

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)


def test_registry_registers_and_finds_channels() -> None:
    registry = ChannelRegistry()
    echo = EchoChannel()
    registry.register(echo)

    assert "echo" in registry
    assert registry.names() == ["echo"]
    assert registry.get("echo") is echo
    assert registry.get("whatsapp") is None
    assert isinstance(echo, ChannelAdapter)


def test_registry_rejects_nameless_channels() -> None:
    registry = ChannelRegistry()

    class Nameless:
        name = ""

        async def send(self, message: OutboundMessage) -> None:
            return None

    try:
        registry.register(Nameless())  # type: ignore[arg-type]
    except ValueError:
        return
    raise AssertionError("expected ValueError for a channel without a name")


def test_inbound_and_outbound_messages() -> None:
    inbound = InboundMessage(
        channel="telegram",
        sender_id="42",
        text="open settings",
        thread_id="42",
    )
    assert inbound.channel == "telegram"
    assert inbound.attachments == []

    outbound = OutboundMessage(recipient="42", text="done")
    assert outbound.reply_to is None
    assert outbound.metadata == {}


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
    print("channels tests passed")
