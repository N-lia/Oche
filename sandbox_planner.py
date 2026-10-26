"""Drive the OYI phone-use loop from a Nebius Sandbox.

The sandbox runs the *decision loop* for one phone command: it asks the
gateway for the next screen, calls the vision model, and posts the raw model
output back. The gateway validates the action and executes it on the phone, so
the protocol stays in one place while the planner runs in an isolated Nebius
environment (with its own copy of the model credentials).

Environment:
    NEBIUS_API_KEY        token factory key (used inside the sandbox)
    NEBIUS_PROJECT_ID     project that runs Sandboxes
    OYI_SANDBOX_IMAGE     sandbox image (default: oyi-sandbox:latest)
    OYI_SANDBOX_AGENT     path of the in-sandbox agent program
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from sandbox_image_contract import BUILD_HINT, image_ref as configured_image

LOGGER = logging.getLogger("oyi.sandbox.planner")

AGENT_SCRIPT = Path(__file__).with_name("sandbox_agent") / "phone_agent.py"


def sandbox_planner_enabled() -> bool:
    return (
        os.getenv("OYI_SANDBOX_PLANNER", "0").strip() in {"1", "true", "yes", "on"}
        and bool(os.getenv("NEBIUS_PROJECT_ID", "").strip())
        and bool(os.getenv("NEBIUS_API_KEY", "").strip())
    )


def _agent_source() -> str:
    path = Path(os.getenv("OYI_SANDBOX_AGENT", str(AGENT_SCRIPT)))
    if not path.exists():
        raise FileNotFoundError(f"sandbox agent program not found: {path}")
    return path.read_text(encoding="utf-8")


def _command() -> str:
    return "python3 -u /home/agent/phone_agent.py"


async def run_sandbox_planner(
    *,
    gateway_url: str,
    gateway_token: str,
    job_id: str,
    model: str,
    max_steps: int = 16,
    timeout_s: float = 600.0,
) -> dict[str, Any]:
    """Run the phone-use agent inside a Nebius Sandbox.

    Returns the sandbox operation result: exit code, stdout and stderr.
    """
    from contree_sdk import Contree

    client = Contree()
    ref = configured_image()
    try:
        image = await client.images.use(ref, strict=True)
    except Exception as exc:  # noqa: BLE001 - make a missing image actionable
        raise RuntimeError(f"Sandbox image {ref!r} is unavailable. {BUILD_HINT}") from exc

    env = {
        "OYI_GATEWAY_URL": gateway_url,
        "OYI_GATEWAY_TOKEN": gateway_token,
        "OYI_JOB_ID": job_id,
        "OYI_VISION_MODEL": model,
        "OYI_MAX_STEPS": str(max_steps),
        "NEBIUS_API_KEY": os.environ["NEBIUS_API_KEY"],
    }

    operation = image.run(
        shell=_command(),
        env=env,
        files={"/home/agent/phone_agent.py": _agent_source()},
        disposable=True,
        timeout=timeout_s,
    )
    result = await operation
    LOGGER.info(
        "sandbox planner for job %s finished: exit=%s",
        job_id,
        getattr(result, "exit_code", "?"),
    )
    return {
        "exit_code": getattr(result, "exit_code", None),
        "stdout": (getattr(result, "stdout", "") or "")[-4000:],
        "stderr": (getattr(result, "stderr", "") or "")[-4000:],
    }


def describe_environment() -> str:
    """A one-line summary for logs (no secrets)."""
    return (
        f"planner={'sandbox' if sandbox_planner_enabled() else 'in-process'} "
        f"project={os.getenv('NEBIUS_PROJECT_ID', '-')} "
        f"image={configured_image()}"
    )
