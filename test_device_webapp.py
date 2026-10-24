#!/usr/bin/env python3
"""End-to-end smoke test for the device endpoints on the real FastAPI app.

It connects a fake phone over the WebSocket, runs a command with a stubbed
vision planner and checks the HTTP API surface.

Run:
    .venv/bin/python test_device_webapp.py
"""

from __future__ import annotations


import os

os.environ.setdefault("NEBIUS_API_KEY", "test-key")
os.environ["OYI_DEVICE_TOKEN"] = "device-secret"
os.environ["OYI_DEVICE_API_TOKEN"] = "api-secret"

from fastapi.testclient import TestClient  # noqa: E402

from device_gateway import DeviceConnection, device_gateway  # noqa: E402
from device_protocol import Action, DeviceHello, Observation  # noqa: E402
import webapp_server  # noqa: E402  (registers the routes)


class StubVision:
    def __init__(self) -> None:
        self.calls = 0

    async def next_action(self, **kwargs) -> Action:
        self.calls += 1
        if self.calls == 1:
            return Action(type="tap_text", text="Wi-Fi", reason="open Wi-Fi")
        return Action(type="done", message="Opened Wi-Fi settings.")

    async def aclose(self) -> None:
        return None


def _run() -> None:
    stub = StubVision()
    device_gateway._vision = stub  # type: ignore[assignment]

    with TestClient(webapp_server.app) as client:
        unauthorized = client.get("/api/device/devices")
        assert unauthorized.status_code == 401, unauthorized.status_code

        headers = {"Authorization": "Bearer api-secret"}
        empty = client.get("/api/device/devices", headers=headers)
        assert empty.status_code == 200, empty.text
        assert empty.json()["devices"] == []

        health = client.get("/api/device/health", headers=headers)
        assert health.status_code == 200, health.text
        assert health.json()["model"] == "nvidia/Nemotron-3.5-Super-VL"

        offline = client.post(
            "/api/device/command",
            headers=headers,
            json={"text": "open settings"},
        )
        assert offline.status_code == 200, offline.text
        assert offline.json()["status"] == "error"
        assert "No Android phone" in offline.json()["message"]

        # Now connect a fake phone and run a real command through the loop.
        with client.websocket_connect(
            "/ws/device",
            headers={"Authorization": "Bearer device-secret"},
        ) as socket:
            socket.send_json(
                {
                    "type": "hello",
                    "device_id": "dev_smoke",
                    "name": "Itel City 100",
                    "model": "C671L",
                    "android_sdk": 34,
                    "app_version": "0.1.0",
                    "capabilities": ["accessibility"],
                }
            )
            ack = socket.receive_json()
            assert ack["type"] == "hello_ack", ack

            devices = client.get("/api/device/devices", headers=headers).json()["devices"]
            assert len(devices) == 1, devices
            assert devices[0]["device_id"] == "dev_smoke"

            def drive() -> None:
                frame = socket.receive_json()
                assert frame["type"] == "command", frame
                socket.send_json(
                    {
                        "type": "observation",
                        "command_id": frame["command_id"],
                        "step": 0,
                        "package": "com.android.settings",
                        "app_label": "Settings",
                        "tree": [{"text": "Wi-Fi"}],
                        "screenshot": "",
                        "width": 720,
                        "height": 1600,
                    }
                )
                action_frame = socket.receive_json()
                assert action_frame["type"] == "action", action_frame
                assert action_frame["action"]["type"] == "tap_text", action_frame
                socket.send_json(
                    {
                        "type": "observation",
                        "command_id": frame["command_id"],
                        "step": 1,
                        "package": "com.android.settings",
                        "app_label": "Settings",
                        "tree": [{"text": "Wi-Fi"}],
                        "screenshot": "",
                        "width": 720,
                        "height": 1600,
                        "last_action_ok": True,
                    }
                )
                done_frame = socket.receive_json()
                assert done_frame["type"] == "command_done", done_frame

            import threading  # noqa: PLC0415

            thread = threading.Thread(target=drive)
            thread.start()

            result = client.post(
                "/api/device/command",
                headers=headers,
                json={"text": "open wifi settings", "max_steps": 4, "timeout_s": 20},
            )
            thread.join()

            assert result.status_code == 200, result.text
            payload = result.json()
            assert payload["status"] == "completed", payload
            assert "Wi-Fi" in payload["message"], payload
            assert payload["steps"] == 1, payload

            # Status request: the phone answers with what it is doing.
            def drive_status() -> None:
                frame = socket.receive_json()
                assert frame["type"] == "status_request", frame
                socket.send_json(
                    {
                        "type": "status_response",
                        "request_id": frame["request_id"],
                        "package": "com.android.settings",
                        "app_label": "Settings",
                        "elapsed_ms": 65_000,
                        "today_ms": 600_000,
                        "screen_on": True,
                        "top_apps": [
                            {"package": "com.android.settings", "label": "Settings", "ms": 600_000}
                        ],
                    }
                )

            status_thread = threading.Thread(target=drive_status)
            status_thread.start()
            status = client.get("/api/device/status", headers=headers)
            status_thread.join()

            assert status.status_code == 200, status.text
            body = status.json()
            assert body["app_label"] == "Settings", body
            assert body["elapsed_ms"] == 65_000, body
            assert body["top_apps"][0]["label"] == "Settings", body


if __name__ == "__main__":
    _run()
    print("device webapp smoke test passed")
