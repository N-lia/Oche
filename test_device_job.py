#!/usr/bin/env python3
"""Tests for the sandbox-driven job protocol.

A fake phone connects over the WebSocket and plays the part of the real
device; the test plays the part of the planner that runs inside a Nebius
Sandbox (fetch observation -> post raw model output -> post result).

Run:
    .venv/bin/python test_device_job.py
"""

from __future__ import annotations

import json
import os
import threading

os.environ.setdefault("NEBIUS_API_KEY", "test-key")
os.environ.setdefault("OYI_DEVICE_TOKEN", "test-token")
os.environ.setdefault("OYI_DEVICE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient  # noqa: E402

import webapp_server  # noqa: E402
from device_gateway import device_gateway  # noqa: E402

HEADERS = {"Authorization": "Bearer test-token"}

ELEMENT = {
    "index": 1,
    "text": "Display & Brightness",
    "kind": "Text",
    "class": "android.widget.TextView",
    "id": "",
    "bounds": [0, 100, 720, 200],
}


def _observation(command_id: str, step: int) -> dict:
    return {
        "type": "observation",
        "command_id": command_id,
        "step": step,
        "package": "com.android.settings",
        "app_label": "Settings",
        "elements": [ELEMENT],
        "element_text": "[1] Text: 'Display & Brightness' | Bounds: [0,62][1000,125]",
        "screenshot": "",
        "width": 720,
        "height": 1600,
        "last_action_ok": True,
        "error": "",
        "action_result": "",
    }


def _run() -> None:
    with TestClient(webapp_server.app) as client:
        with client.websocket_connect(
            "/ws/device",
            headers={"Authorization": "Bearer test-token"},
        ) as socket:
            socket.send_json(
                {
                    "type": "hello",
                    "device_id": "dev_job",
                    "name": "itel C671L",
                    "model": "itel C671L",
                    "android_sdk": 34,
                    "app_version": "0.1.0",
                    "capabilities": ["accessibility"],
                }
            )
            ack = socket.receive_json()
            assert ack["type"] == "hello_ack", ack

            # 1. the planner (sandbox) creates a job
            created = client.post(
                "/api/device/job",
                headers=HEADERS,
                json={"text": "open the Display & Brightness settings", "max_steps": 6},
            )
            assert created.status_code == 200, created.text
            job_id = created.json()["job_id"]

            # the phone received the command and answers with a screen
            command = socket.receive_json()
            assert command["type"] == "command", command
            assert command["command_id"] == job_id, command
            socket.send_json(_observation(job_id, 0))

            # 2. the planner asks for the next screen and gets the prompt
            seen: list[dict] = []

            def planner() -> None:
                screen = client.get(
                    f"/api/device/job/{job_id}/observation", headers=HEADERS
                ).json()
                seen.append(screen)
                # the planner's model output, in the phone-use dialect
                verdict = client.post(
                    f"/api/device/job/{job_id}/action",
                    headers=HEADERS,
                    json={
                        "raw": json.dumps(
                            {
                                "action_type": "click",
                                "action": "the text 'Display & Brightness' in the list",
                                "action_ui_element": "the text 'Display & Brightness' in the list",
                                "x": 0.5,
                                "y": 0.2,
                            }
                        )
                    },
                ).json()
                assert verdict.get("finished") is False, verdict
                # the phone executes and reports back
                socket.send_json(_observation(job_id, 1))
                client.post(
                    f"/api/device/job/{job_id}/result",
                    headers=HEADERS,
                    json={"status": "completed", "message": "Display & Brightness is open."},
                )

            thread = threading.Thread(target=planner)
            thread.start()

            # the action the gateway derived reaches the phone
            action = socket.receive_json()
            assert action["type"] == "action", action
            assert action["action"]["type"] == "tap", action
            assert action["action"]["element_text"] == (
                "the text 'Display & Brightness' in the list"
            ), action
            thread.join()

            # the planner saw a rendered prompt with the element list
            assert seen, "the planner never received an observation"
            messages = seen[0]["messages"]
            assert messages[0]["role"] == "system", messages[0]["role"]
            user_text = messages[1]["content"][0]["text"]
            assert "Visible UI Elements" in user_text, user_text[:200]
            assert "Display & Brightness" in user_text, user_text[:400]

            # 3. the job is finished and reports the result
            result = client.post(
                f"/api/device/job/{job_id}/result",
                headers=HEADERS,
                json={"status": "completed", "message": "Display & Brightness is open."},
            )
            assert result.status_code == 200, result.text

            # an unknown job is a 404
            missing = client.get("/api/device/job/nope/observation", headers=HEADERS)
            assert missing.status_code == 404, missing.text


if __name__ == "__main__":
    _run()
    print("device job tests passed")
