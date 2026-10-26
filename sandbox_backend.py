"""Nebius Sandboxes backend for the OYI agent.

OYI's agent runs on deepagents. Until now the agent used a local filesystem
backend with the shell ``execute`` tool disabled, because generated code must
never run inside the webapp process.

Nebius Sandboxes give the same agent an isolated place to execute code:
the tool runs in a Nebius-managed environment with only the network access we
choose to give it, and the agent's files live in that environment instead of
in the webapp container.

Required environment:

    NEBIUS_API_KEY        token factory key
    NEBIUS_PROJECT_ID     project that runs Sandboxes

Optional:

    OYI_SANDBOX_BACKEND   "1" to use the sandbox backend (default: off)
    OYI_SANDBOX_IMAGE     base image for the sandbox (default: python:3.12-slim)

See https://docs.tokenfactory.nebius.com/sandboxes/start/python-quickstart
"""

from __future__ import annotations

import logging
import os
from typing import Any

LOGGER = logging.getLogger("oyi.sandbox")

DEFAULT_IMAGE = "python:3.12-slim"


def sandbox_configured() -> bool:
    """True when the process has the credentials Sandboxes needs."""
    return bool(os.getenv("NEBIUS_PROJECT_ID", "").strip()) and bool(
        os.getenv("NEBIUS_API_KEY", "").strip()
    )


def sandbox_requested() -> bool:
    return os.getenv("OYI_SANDBOX_BACKEND", "0").strip() in {"1", "true", "yes", "on"}


def build_sandbox_backend(image_ref: str | None = None) -> Any:
    """Create a deepagents backend backed by a Nebius Sandbox."""
    from contree_sdk import ContreeSync
    from contree_sdk.langchain.sandbox import ContreeSandbox
    from contree_sdk.sdk.objects.session import ContreeSession

    client = ContreeSync()
    image = client.images.use(image_ref or DEFAULT_IMAGE, strict=True)
    LOGGER.info("Nebius sandbox backend ready (image=%s)", image_ref or DEFAULT_IMAGE)
    return ContreeSandbox(ContreeSession(image))
