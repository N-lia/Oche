"""Registry for messaging adapters.

Telegram is the first adapter; adding WhatsApp/Discord/SMS means registering a
new ``ChannelAdapter`` implementation, not touching the Android device code.
"""

from __future__ import annotations

from channels.base import ChannelAdapter


class ChannelRegistry:
    def __init__(self) -> None:
        self._channels: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        if not adapter.name:
            raise ValueError("channel adapter must have a name")
        self._channels[adapter.name] = adapter

    def get(self, name: str) -> ChannelAdapter | None:
        return self._channels.get(name)

    def names(self) -> list[str]:
        return sorted(self._channels)

    def __contains__(self, name: str) -> bool:
        return name in self._channels
