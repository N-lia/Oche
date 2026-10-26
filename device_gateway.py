"""In-memory registry and command loop for paired Android devices.

The gateway is intentionally process-local for the first version.  A command is
a small state machine:

    server: command
    phone:  observation (step 0)
    server: action
    phone:  observation (step 1)
    ...
    server: command_done

Swapping this for Redis/Postgres later only changes the registry; the wire
protocol stays the same.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from device_protocol import (
    Action,
    CommandResult,
    DeviceHello,
    DeviceSummary,
    Observation,
    action_for_device,
)
from vision_agent import VisionAgent

LOGGER = logging.getLogger("oyi.device")


class SocketLike(Protocol):
    async def send_json(self, data: Any) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class DeviceGatewayError(RuntimeError):
    """Base class for device gateway errors."""


class DeviceOfflineError(DeviceGatewayError):
    """No suitable phone is connected."""


@dataclass
class DeviceConnection:
    websocket: SocketLike
    hello: DeviceHello
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    connected: bool = True
    active_command_id: str = ""
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)

    def summary(self) -> DeviceSummary:
        return DeviceSummary(
            device_id=self.hello.device_id,
            name=self.hello.name,
            model=self.hello.model,
            android_sdk=self.hello.android_sdk,
            app_version=self.hello.app_version,
            capabilities=list(self.hello.capabilities),
            connected=self.connected,
            last_seen=self.last_seen,
            active_command_id=self.active_command_id,
        )


@dataclass
class CommandSession:
    command_id: str
    device_id: str
    instruction: str
    max_steps: int
    thread_id: str
    created_at: float = field(default_factory=time.time)
    status: str = "running"
    message: str = ""
    error: str = ""
    last_action: Action | None = None
    last_observation_step: int = -1
    history: list[dict[str, Any]] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def finish(self, status: str, message: str = "", error: str = "") -> None:
        if self.status != "running":
            return
        # "done" and "ask_user" both terminate the command; they are not
        # transport errors.  Keep the public status set small.
        if status in {"done", "ask_user"}:
            status = "completed"
        self.status = status
        self.message = message
        self.error = error
        self.done.set()

    def result(self) -> CommandResult:
        steps = max(0, self.last_observation_step)
        if self.status == "running":
            return CommandResult(
                command_id=self.command_id,
                device_id=self.device_id,
                status="error",
                message="Command is still running.",
                steps=steps,
            )
        return CommandResult(
            command_id=self.command_id,
            device_id=self.device_id,
            status=self.status,  # type: ignore[arg-type]
            message=self.message,
            steps=steps,
            error=self.error,
        )


class DeviceGateway:
    def __init__(self, vision: VisionAgent | None = None) -> None:
        self._vision = vision
        self._devices: dict[str, DeviceConnection] = {}
        self._commands: dict[str, CommandSession] = {}
        self._status_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def register(self, connection: DeviceConnection) -> None:
        async with self._lock:
            previous = self._devices.get(connection.hello.device_id)
            self._devices[connection.hello.device_id] = connection
        if previous is not None and previous is not connection:
            try:
                await previous.send(
                    {
                        "type": "error",
                        "message": "Replaced by a newer connection for this device.",
                    }
                )
                await previous.websocket.close(code=1000, reason="replaced")
            except Exception:  # noqa: BLE001 - old connection may be gone
                pass
            await self._finish_device_commands(
                previous.hello.device_id,
                "error",
                "Phone reconnected; previous command was interrupted.",
            )
        LOGGER.info(
            "Device connected id=%s name=%s sdk=%s",
            connection.hello.device_id,
            connection.hello.name or "?",
            connection.hello.android_sdk,
        )

    async def unregister(self, connection: DeviceConnection) -> None:
        async with self._lock:
            if self._devices.get(connection.hello.device_id) is connection:
                self._devices.pop(connection.hello.device_id, None)
        connection.connected = False
        await self._finish_device_commands(
            connection.hello.device_id,
            "error",
            "Phone disconnected before the command finished.",
        )
        LOGGER.info("Device disconnected id=%s", connection.hello.device_id)

    def get(self, device_id: str) -> DeviceConnection | None:
        return self._devices.get(device_id)

    def list_devices(self) -> list[DeviceSummary]:
        return [connection.summary() for connection in self._devices.values()]

    def _choose_device(self, device_id: str) -> DeviceConnection:
        requested = (device_id or "").strip()
        if requested and requested != "default":
            connection = self._devices.get(requested)
            if connection is not None and connection.connected:
                return connection
            LOGGER.warning("Unknown device id %r; falling back to the connected phone", requested)
        online = [connection for connection in self._devices.values() if connection.connected]
        if not online:
            raise DeviceOfflineError("No Android phone is connected to OYI.")
        if len(online) > 1:
            ids = ", ".join(connection.hello.device_id for connection in online)
            raise DeviceGatewayError(f"Multiple phones are connected; specify device_id ({ids}).")
        return online[0]

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def send_command(
        self,
        *,
        text: str,
        device_id: str = "",
        max_steps: int = 8,
        thread_id: str = "device",
    ) -> CommandSession:
        connection = self._choose_device(device_id)
        if connection.active_command_id:
            raise DeviceGatewayError("The phone is already running another command.")

        command = CommandSession(
            command_id=f"cmd_{uuid.uuid4().hex[:12]}",
            device_id=connection.hello.device_id,
            instruction=text,
            max_steps=max_steps,
            thread_id=thread_id,
        )
        self._commands[command.command_id] = command
        connection.active_command_id = command.command_id
        connection.last_seen = time.time()

        try:
            await connection.send(
                {
                    "type": "command",
                    "command_id": command.command_id,
                    "text": text,
                    "max_steps": max_steps,
                }
            )
        except Exception as exc:  # noqa: BLE001 - transport error
            command.finish("error", error=f"Could not send command to phone: {exc}")
            connection.active_command_id = ""
        return command

    async def execute_command(
        self,
        *,
        text: str,
        device_id: str = "",
        max_steps: int = 8,
        timeout_s: float = 180.0,
        thread_id: str = "device",
    ) -> CommandResult:
        try:
            command = await self.send_command(
                text=text,
                device_id=device_id,
                max_steps=max_steps,
                thread_id=thread_id,
            )
        except DeviceGatewayError as exc:
            return CommandResult(
                command_id=f"cmd_{uuid.uuid4().hex[:12]}",
                device_id=device_id,
                status="error",
                message=str(exc),
                error=str(exc),
            )

        if command.status == "running":
            try:
                await asyncio.wait_for(command.done.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                await self.cancel(
                    command.command_id,
                    reason=f"Phone did not finish within {timeout_s:.0f}s.",
                    status="timeout",
                )
        return command.result()

    async def cancel(
        self,
        command_id: str,
        reason: str = "Cancelled",
        status: str = "cancelled",
    ) -> bool:
        command = self._commands.get(command_id)
        if command is None or command.status != "running":
            return False
        command.finish(status, message=reason)
        connection = self._devices.get(command.device_id)
        if connection is not None:
            connection.active_command_id = ""
            try:
                await connection.send(
                    {
                        "type": "cancel",
                        "command_id": command_id,
                        "message": reason,
                    }
                )
            except Exception:  # noqa: BLE001 - best effort
                LOGGER.debug("Could not deliver cancel to device", exc_info=True)
        return True

    async def _finish_device_commands(self, device_id: str, status: str, message: str) -> None:
        for command in list(self._commands.values()):
            if command.device_id == device_id and command.status == "running":
                command.finish(status, message=message)
        connection = self._devices.get(device_id)
        if connection is not None:
            connection.active_command_id = ""

    # ------------------------------------------------------------------
    # Status requests
    # ------------------------------------------------------------------

    async def request_status(
        self,
        *,
        device_id: str = "",
        timeout_s: float = 6.0,
    ) -> dict[str, Any]:
        """Ask the phone what it is doing right now.

        Returns the phone's own report: current app, time in it, today's
        total for that app and the top apps so far.
        """
        connection = self._choose_device(device_id)
        request_id = f"status_{uuid.uuid4().hex[:10]}"
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._status_waiters[request_id] = waiter

        try:
            await connection.send(
                {
                    "type": "status_request",
                    "request_id": request_id,
                }
            )
        except Exception as exc:  # noqa: BLE001 - transport error
            self._status_waiters.pop(request_id, None)
            raise DeviceOfflineError(f"Could not reach the phone: {exc}") from exc

        try:
            return await asyncio.wait_for(waiter, timeout=timeout_s)
        except asyncio.TimeoutError:
            raise DeviceGatewayError("The phone did not answer the status request.") from None
        finally:
            self._status_waiters.pop(request_id, None)

    # ------------------------------------------------------------------
    # Incoming device messages
    # ------------------------------------------------------------------

    async def handle_message(self, connection: DeviceConnection, raw: str) -> None:
        connection.last_seen = time.time()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await connection.send({"type": "error", "message": "Invalid JSON frame."})
            return
        if not isinstance(payload, dict):
            await connection.send({"type": "error", "message": "Expected a JSON object."})
            return

        message_type = payload.get("type")
        if message_type == "observation":
            try:
                observation = Observation.model_validate(payload)
            except Exception as exc:  # noqa: BLE001 - report validation to phone
                await connection.send({"type": "error", "message": f"Invalid observation: {exc}"})
                return
            await self.on_observation(connection, observation)
        elif message_type == "command_done":
            command = self._commands.get(str(payload.get("command_id", "")))
            if command is not None:
                command.finish(
                    "completed",
                    message=str(payload.get("message", "Phone stopped the command.")),
                )
                connection.active_command_id = ""
        elif message_type == "status_response":
            request_id = str(payload.get("request_id", ""))
            waiter = self._status_waiters.pop(request_id, None)
            if waiter is not None and not waiter.done():
                waiter.set_result(payload)
        elif message_type == "ping":
            await connection.send({"type": "pong", "time": time.time()})
        elif message_type == "event":
            # Reserved for foreground-app/usage events.  The phone keeps the
            # authoritative local DB; the server only needs summaries on demand.
            LOGGER.debug(
                "Device event id=%s type=%s",
                connection.hello.device_id,
                payload.get("event"),
            )
        else:
            await connection.send(
                {"type": "error", "message": f"Unknown message type: {message_type!r}"}
            )

    async def on_observation(self, connection: DeviceConnection, observation: Observation) -> None:
        command = self._commands.get(observation.command_id)
        if command is None:
            await connection.send(
                {
                    "type": "command_done",
                    "command_id": observation.command_id,
                    "status": "error",
                    "message": "Unknown or expired command.",
                }
            )
            return
        if command.device_id != connection.hello.device_id:
            await connection.send(
                {
                    "type": "command_done",
                    "command_id": observation.command_id,
                    "status": "error",
                    "message": "Command does not belong to this device.",
                }
            )
            return
        if command.status != "running":
            return

        connection.last_seen = time.time()
        recovering = False
        if observation.error:
            # A blocked action (the element moved, the tap missed) is handed
            # back to the planner so it can re-observe and choose again, the
            # way ARTEMIS recovers from a failed action. Only a broken
            # accessibility service ends the command.
            if "accessibility" in observation.error.lower():
                await self._finish_command(
                    command,
                    connection,
                    "error",
                    message=f"Phone error: {observation.error}",
                    error=observation.error,
                )
                return
            command.history.append(
                {
                    "step": observation.step,
                    "action": {},
                    "ok": False,
                    "error": observation.error,
                    "result": observation.action_result or observation.error,
                }
            )
            command.last_observation_step = observation.step
            command.last_action = None
            if observation.step >= command.max_steps:
                await self._finish_command(
                    command,
                    connection,
                    "completed",
                    message=f"Stopped after the {command.max_steps}-step limit.",
                )
                return
            # The planner sees PREVIOUS RESULT and picks a different action.
            recovering = True

        if not recovering:
            # Duplicate/retry: resend the last action instead of asking the model
            # to make a new decision from the same screen.
            if observation.step == command.last_observation_step and command.last_action is not None:
                await self._send_action(connection, command, command.last_action)
                return
            if observation.step < command.last_observation_step:
                return
            if observation.step != command.last_observation_step + 1 and observation.step != 0:
                LOGGER.warning(
                    "Ignoring out-of-order observation command=%s got=%s expected=%s",
                    command.command_id,
                    observation.step,
                    command.last_observation_step + 1,
                )
                return

            command.last_observation_step = observation.step
        if observation.step >= command.max_steps:
            await self._finish_command(
                command,
                connection,
                "completed",
                message=f"Stopped after the {command.max_steps}-step limit.",
            )
            return

        vision = self._vision
        if vision is None:
            vision = VisionAgent()
            self._vision = vision
        try:
            action = await vision.next_action(
                instruction=command.instruction,
                observation=observation,
                step=observation.step,
                history=command.history,
            )
        except Exception as exc:  # noqa: BLE001 - surface a useful phone error
            LOGGER.exception("Vision planner failed command=%s", command.command_id)
            await self._finish_command(
                command,
                connection,
                "error",
                message=f"Vision planner failed: {exc}",
                error=str(exc),
            )
            return

        if action.type in {"done", "ask_user"}:
            message = action.message.strip() or (
                "Done." if action.type == "done" else "I need more information to continue."
            )
            await self._finish_command(command, connection, "completed", message=message)
            return

        command.last_action = action
        LOGGER.info(
            "command=%s step=%s action=%s",
            command.command_id,
            observation.step,
            action_for_device(action),
        )
        command.history.append(
            {
                "step": observation.step,
                "action": action_for_device(action),
                "ok": observation.last_action_ok,
                "error": observation.error,
                "result": observation.action_result,
            }
        )
        await self._send_action(connection, command, action)

    async def _send_action(
        self,
        connection: DeviceConnection,
        command: CommandSession,
        action: Action,
    ) -> None:
        try:
            await connection.send(
                {
                    "type": "action",
                    "command_id": command.command_id,
                    "step": command.last_observation_step,
                    "action": action_for_device(action),
                    "message": action.reason,
                }
            )
        except Exception as exc:  # noqa: BLE001 - transport error
            await self._finish_command(
                command,
                connection,
                "error",
                message=f"Could not send action to phone: {exc}",
                error=str(exc),
            )

    async def _finish_command(
        self,
        command: CommandSession,
        connection: DeviceConnection,
        status: str,
        *,
        message: str = "",
        error: str = "",
    ) -> None:
        command.finish(status, message=message, error=error)
        connection.active_command_id = ""
        try:
            await connection.send(
                {
                    "type": "command_done",
                    "command_id": command.command_id,
                    "status": command.status,
                    "message": command.message,
                    "error": command.error,
                }
            )
        except Exception:  # noqa: BLE001 - phone may have disconnected
            LOGGER.debug("Could not deliver command_done", exc_info=True)

    async def aclose(self) -> None:
        for command in list(self._commands.values()):
            if command.status == "running":
                command.finish("cancelled", message="Server shutting down.")
        for connection in list(self._devices.values()):
            try:
                await connection.websocket.close(code=1001, reason="server shutdown")
            except Exception:  # noqa: BLE001 - best effort
                pass
            connection.connected = False
        self._devices.clear()
        if self._vision is not None:
            await self._vision.aclose()


# Process-local singleton used by webapp_server.  Tests construct their own
# DeviceGateway with a fake vision planner.
device_gateway = DeviceGateway()
