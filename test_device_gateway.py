#!/usr/bin/env python3
"""Tests for the in-memory device command state machine.

Run:
    .venv/bin/python test_device_gateway.py
"""

from __future__ import annotations

import asyncio

from device_gateway import CommandSession, DeviceConnection, DeviceGateway
from device_protocol import Action, DeviceHello, Observation


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


class FakeVision:
    def __init__(self, actions: list[Action]) -> None:
        self.actions = list(actions)
        self.calls = 0
        self.closed = False

    async def next_action(self, **kwargs) -> Action:
        self.calls += 1
        if not self.actions:
            return Action(type="done", message="Finished.")
        return self.actions.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def _hello() -> DeviceHello:
    return DeviceHello(
        device_id="dev_test",
        name="Itel City 100",
        model="C671L",
        android_sdk=34,
        app_version="0.1.0",
    )


def _observation(command_id: str, step: int) -> Observation:
    return Observation(
        command_id=command_id,
        step=step,
        package="com.android.settings",
        app_label="Settings",
        tree=[{"text": "Wi-Fi", "bounds": [0, 100, 720, 200]}],
        screenshot="",
        width=720,
        height=1600,
    )


async def _run() -> None:
    socket = FakeSocket()
    vision = FakeVision(
        [
            Action(type="tap_text", text="Wi-Fi", reason="open Wi-Fi"),
            Action(type="done", message="Opened Wi-Fi settings."),
        ]
    )
    gateway = DeviceGateway(vision=vision)  # type: ignore[arg-type]
    connection = DeviceConnection(websocket=socket, hello=_hello())  # type: ignore[arg-type]
    await gateway.register(connection)

    session = await gateway.send_command(text="open Wi-Fi settings", max_steps=4)
    assert isinstance(session, CommandSession)
    assert socket.sent[0]["type"] == "command"
    assert socket.sent[0]["text"] == "open Wi-Fi settings"

    await gateway.on_observation(connection, _observation(session.command_id, 0))
    action_frame = socket.sent[-1]
    assert action_frame["type"] == "action"
    assert action_frame["action"]["type"] == "tap_text"
    assert action_frame["action"]["text"] == "Wi-Fi"

    await gateway.on_observation(connection, _observation(session.command_id, 1))
    done_frame = socket.sent[-1]
    assert done_frame["type"] == "command_done"
    assert done_frame["status"] == "completed"
    assert "Wi-Fi" in done_frame["message"]
    assert session.status == "completed"
    assert vision.calls == 2

    await gateway.unregister(connection)
    assert gateway.list_devices() == []
    await gateway.aclose()
    assert vision.closed


async def _run_offline() -> None:
    gateway = DeviceGateway(vision=FakeVision([]))  # type: ignore[arg-type]
    result = await gateway.execute_command(text="open settings")
    assert result.status == "error"
    assert "No Android phone" in result.message
    await gateway.aclose()


if __name__ == "__main__":
    asyncio.run(_run())
    asyncio.run(_run_offline())
    print("device_gateway tests passed")
