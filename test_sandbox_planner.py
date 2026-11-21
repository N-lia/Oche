#!/usr/bin/env python3
"""Tests for starting the phone-use planner in a Nebius Sandbox.

The first test pins the file-upload contract: Contree treats a string file
value as a *local path*, so the program source must be uploaded as bytes. The
old code passed the source text and the SDK raised "File name too long" before
the planner ever ran.

The other tests pin failure propagation: a dead planner must finish the phone
job with the real error instead of making the caller wait for a timeout.

Run:
    .venv/bin/python test_sandbox_planner.py
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("NEBIUS_API_KEY", "test-key")
os.environ.setdefault("OYI_DEVICE_API_TOKEN", "test-token")
os.environ.setdefault("OYI_DEVICE_TOKEN", "device-token")
os.environ.setdefault("OYI_PUBLIC_URL", "https://oyi.example")
os.environ.setdefault("OYI_SANDBOX_IMAGE", "oyi-sandbox:latest")

import contree_sdk  # noqa: E402

import sandbox_planner  # noqa: E402


def test_agent_source_is_uploaded_as_bytes() -> None:
    captured: dict = {}

    class FakeImage:
        def run(self, **kwargs):
            captured.update(kwargs)

            async def complete():
                return SimpleNamespace(exit_code=0, stdout="planner ok", stderr="")

            return complete()

    class FakeImages:
        async def use(self, ref, strict=True):
            captured["image_ref"] = ref
            captured["strict"] = strict
            return FakeImage()

    class FakeContree:
        def __init__(self) -> None:
            self.images = FakeImages()

    with patch.object(contree_sdk, "Contree", FakeContree):
        result = asyncio.run(
            sandbox_planner.run_sandbox_planner(
                gateway_url="https://oyi.example",
                gateway_token="secret",
                job_id="job_test",
                model="nvidia/Nemotron-3.5-Super-VL",
                max_steps=4,
                timeout_s=30,
            )
        )

    assert result == {"exit_code": 0, "stdout": "planner ok", "stderr": ""}, result
    assert captured["image_ref"] == "oyi-sandbox:latest", captured
    assert captured["strict"] is True, captured
    assert captured["shell"] == "python3 -u /tmp/oyi_phone_agent.py", captured
    source = sandbox_planner._agent_source()
    assert captured["files"] == {
        sandbox_planner.AGENT_REMOTE_PATH: source.encode("utf-8")
    }, captured["files"].keys()
    env = captured["env"]
    assert env["OYI_JOB_ID"] == "job_test", env
    assert env["OYI_GATEWAY_URL"] == "https://oyi.example", env
    assert env["OYI_GATEWAY_TOKEN"] == "secret", env
    assert env["OYI_VISION_MODEL"] == "nvidia/Nemotron-3.5-Super-VL", env
    assert env["OYI_MAX_STEPS"] == "4", env
    assert env["NEBIUS_API_KEY"] == "test-key", env


class _FakeGateway:
    """Minimal gateway: resolves the job when the planner fails."""

    def __init__(self) -> None:
        self.job = SimpleNamespace(id="job_test", device_id="dev_test")
        self.result = None
        self.finished: list[tuple[str, str]] = []

    async def create_job(self, *, device_id, instruction, max_steps):
        self.result = asyncio.get_running_loop().create_future()
        return self.job

    async def job_result(self, job_id, timeout=300.0):
        assert job_id == "job_test", job_id
        return await self.result

    async def finish_job(self, job_id, *, status, message):
        assert job_id == "job_test", job_id
        self.finished.append((status, message))
        if not self.result.done():
            from device_protocol import CommandResult

            self.result.set_result(
                CommandResult(
                    command_id=job_id,
                    device_id="dev_test",
                    status=status,
                    message=message,
                )
            )
        return {"ok": True}


def _run_with_planner(planner) -> tuple[dict, _FakeGateway]:
    import webapp_server
    from device_protocol import CommandRequest

    gateway = _FakeGateway()
    original_gateway = webapp_server.device_gateway
    webapp_server.device_gateway = gateway
    try:
        with patch.object(webapp_server, "run_sandbox_planner", planner):
            result = asyncio.run(
                webapp_server._run_command_in_sandbox(
                    CommandRequest(text="open settings", timeout_s=30)
                )
            )
    finally:
        webapp_server.device_gateway = original_gateway
    return result, gateway


def test_planner_exception_finishes_job_immediately() -> None:
    async def planner(**kwargs):
        raise RuntimeError("upload exploded")

    result, gateway = _run_with_planner(planner)

    assert result["status"] == "error", result
    assert "upload exploded" in result["message"], result
    assert gateway.finished and gateway.finished[0][0] == "error", gateway.finished


def test_planner_exit_without_result_reports_stderr() -> None:
    async def planner(**kwargs):
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "observation failed: HTTP Error 404",
        }

    result, gateway = _run_with_planner(planner)

    assert result["status"] == "error", result
    assert "exited without a result" in result["message"], result
    assert "observation failed" in result["message"], result
    assert gateway.finished and gateway.finished[0][0] == "error", gateway.finished


if __name__ == "__main__":
    test_agent_source_is_uploaded_as_bytes()
    test_planner_exception_finishes_job_immediately()
    test_planner_exit_without_result_reports_stderr()
    print("sandbox planner tests passed")
