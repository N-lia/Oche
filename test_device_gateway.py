#!/usr/bin/env python3
"""Tests for the in-memory device command state machine.

Run:
    .venv/bin/python test_device_gateway.py
"""

from __future__ import annotations

import asyncio
import json

from device_gateway import (
    CommandSession,
    DeviceConnection,
    DeviceGateway,
    DeviceGatewayError,
)
from device_protocol import Action, DeviceHello, Observation


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


class FlakySocket(FakeSocket):
    """A socket that accepts setup frames but loses actions."""

    async def send_json(self, data) -> None:
        if data.get("type") == "action":
            raise RuntimeError("socket gone")
        await super().send_json(data)


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
            Action(type="tap", target=1, reason="open Wi-Fi"),
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
    assert action_frame["action"]["type"] == "tap"
    assert action_frame["action"]["target"] == 1

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


async def _run_job_completion() -> None:
    """A sandbox job must release the phone when it finishes.

    The phone rejects every new command while activeCommand is set.  A job
    that finishes only on the server leaves that flag set forever, so the next
    job is answered with status=busy and then times out.
    """

    socket = FakeSocket()
    gateway = DeviceGateway(vision=FakeVision([]))  # type: ignore[arg-type]
    connection = DeviceConnection(websocket=socket, hello=_hello())  # type: ignore[arg-type]
    await gateway.register(connection)

    job = await gateway.create_job(
        device_id="dev_test", instruction="open settings", max_steps=4
    )
    assert connection.active_command_id == job.id
    assert socket.sent[0]["type"] == "command", socket.sent[0]

    verdict = await gateway.submit_action(
        job.id,
        json.dumps({"action_type": "done", "message": "Settings is open."}),
    )
    assert verdict["finished"] is True, verdict
    assert job.status == "completed", job.status
    assert connection.active_command_id == "", connection.active_command_id
    assert socket.sent[-1]["type"] == "command_done", socket.sent[-1]
    assert socket.sent[-1]["command_id"] == job.id, socket.sent[-1]

    result = await gateway.job_result(job.id, timeout=1)
    assert result.status == "completed", result
    assert result.message == "Settings is open.", result

    # The next job can start immediately; no real phone is left busy.
    second = await gateway.create_job(
        device_id="dev_test", instruction="open Wi-Fi", max_steps=4
    )
    assert second.id != job.id
    await gateway.unregister(connection)
    await gateway.aclose()


async def _run_disconnect_job() -> None:
    """A disconnect resolves the planner and the caller immediately."""

    socket = FakeSocket()
    gateway = DeviceGateway(vision=FakeVision([]))  # type: ignore[arg-type]
    connection = DeviceConnection(websocket=socket, hello=_hello())  # type: ignore[arg-type]
    await gateway.register(connection)
    job = await gateway.create_job(
        device_id="dev_test", instruction="open settings", max_steps=4
    )

    observation_task = asyncio.create_task(
        gateway.next_observation(job.id, timeout=30)
    )
    result_task = asyncio.create_task(gateway.job_result(job.id, timeout=30))
    await asyncio.sleep(0.01)
    await gateway.unregister(connection)

    screen = await asyncio.wait_for(observation_task, 1)
    assert screen["finished"] is True, screen
    assert "disconnected" in screen["message"].lower(), screen

    result = await asyncio.wait_for(result_task, 1)
    assert result.status == "error", result
    assert "disconnected" in result.message.lower(), result
    assert gateway._job_by_device.get("dev_test") is None
    await gateway.aclose()


async def _run_action_send_failure() -> None:
    """A lost action must fail the job, not raise AttributeError."""

    socket = FlakySocket()
    gateway = DeviceGateway(vision=FakeVision([]))  # type: ignore[arg-type]
    connection = DeviceConnection(websocket=socket, hello=_hello())  # type: ignore[arg-type]
    await gateway.register(connection)
    job = await gateway.create_job(
        device_id="dev_test", instruction="open settings", max_steps=4
    )

    observation_task = asyncio.create_task(
        gateway.on_observation(connection, _observation(job.id, 0))
    )
    await asyncio.sleep(0.01)
    verdict = await gateway.submit_action(
        job.id,
        json.dumps({"action_type": "click", "action": "Settings", "x": 0.5, "y": 0.5}),
    )
    assert verdict["finished"] is False, verdict
    await asyncio.wait_for(observation_task, 1)

    result = await gateway.job_result(job.id, timeout=1)
    assert result.status == "error", result
    assert "Could not send action" in result.message, result
    assert connection.active_command_id == "", connection.active_command_id
    await gateway.unregister(connection)
    await gateway.aclose()


async def _run_busy_job() -> None:
    """A phone that is already executing a command is reported, not ignored."""

    socket = FakeSocket()
    gateway = DeviceGateway(vision=FakeVision([]))  # type: ignore[arg-type]
    connection = DeviceConnection(websocket=socket, hello=_hello())  # type: ignore[arg-type]
    await gateway.register(connection)
    job = await gateway.create_job(
        device_id="dev_test", instruction="open settings", max_steps=4
    )

    await gateway.handle_message(
        connection,
        json.dumps(
            {
                "type": "command_done",
                "command_id": job.id,
                "status": "busy",
                "message": "The phone is already running another command.",
            }
        ),
    )
    result = await gateway.job_result(job.id, timeout=1)
    assert result.status == "error", result
    assert "already running" in result.message, result
    assert connection.active_command_id == "", connection.active_command_id
    assert socket.sent[-1]["type"] == "command_done", socket.sent[-1]
    await gateway.unregister(connection)
    await gateway.aclose()


async def _run_job_exclusivity() -> None:
    """Only one job or command may own the phone at a time."""

    socket = FakeSocket()
    gateway = DeviceGateway(vision=FakeVision([]))  # type: ignore[arg-type]
    connection = DeviceConnection(websocket=socket, hello=_hello())  # type: ignore[arg-type]
    await gateway.register(connection)
    job = await gateway.create_job(
        device_id="dev_test", instruction="open settings", max_steps=4
    )

    for operation in (
        lambda: gateway.create_job(
            device_id="dev_test", instruction="open Wi-Fi", max_steps=4
        ),
        lambda: gateway.send_command(text="open Wi-Fi", max_steps=4),
    ):
        try:
            await operation()
        except DeviceGatewayError as exc:
            assert "already running" in str(exc), exc
        else:
            raise AssertionError("a second command was accepted while a job was active")

    await gateway.submit_action(
        job.id, json.dumps({"action_type": "done", "message": "done"})
    )
    await gateway.unregister(connection)
    await gateway.aclose()


async def _run_reconnect_job() -> None:
    """A replacement socket also interrupts the old planner."""

    first = FakeSocket()
    gateway = DeviceGateway(vision=FakeVision([]))  # type: ignore[arg-type]
    old = DeviceConnection(websocket=first, hello=_hello())  # type: ignore[arg-type]
    await gateway.register(old)
    job = await gateway.create_job(
        device_id="dev_test", instruction="open settings", max_steps=4
    )

    second = FakeSocket()
    new = DeviceConnection(websocket=second, hello=_hello())  # type: ignore[arg-type]
    await gateway.register(new)

    result = await gateway.job_result(job.id, timeout=1)
    assert result.status == "error", result
    assert "reconnected" in result.message.lower(), result
    assert gateway.get("dev_test") is new

    # The old WebSocket handler can run its finally block after the new
    # connection registered.  That late unregister must not kill a new job.
    second_job = await gateway.create_job(
        device_id="dev_test", instruction="open Wi-Fi", max_steps=4
    )
    await gateway.unregister(old)
    assert not second_job.done, "late unregister killed the new job"
    assert new.active_command_id == second_job.id
    await gateway.submit_action(
        second_job.id, json.dumps({"action_type": "done", "message": "done"})
    )
    await gateway.unregister(new)
    await gateway.aclose()


if __name__ == "__main__":
    asyncio.run(_run())
    asyncio.run(_run_offline())
    asyncio.run(_run_job_completion())
    asyncio.run(_run_disconnect_job())
    asyncio.run(_run_action_send_failure())
    asyncio.run(_run_busy_job())
    asyncio.run(_run_job_exclusivity())
    asyncio.run(_run_reconnect_job())
    print("device_gateway tests passed")
