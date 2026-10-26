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
    OYI_SANDBOX_IMAGE     sandbox image (default: oyi-sandbox:latest)

The image must provide both toolchains the sandbox actually uses: Python 3
for deepagents' file tools, and Node >= 20 with Pele for Mermaid rendering.
Build it with tools/build_sandbox_image.sh (source: sandbox_image/Dockerfile).

See https://docs.tokenfactory.nebius.com/sandboxes/start/python-quickstart
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sandbox_image_contract import BUILD_HINT, image_ref as configured_image

LOGGER = logging.getLogger("oyi.sandbox")


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

    ref = image_ref or configured_image()
    client = ContreeSync()
    try:
        image = client.images.use(ref, strict=True)
    except Exception as exc:  # noqa: BLE001 - make a missing image actionable
        raise RuntimeError(f"Sandbox image {ref!r} is unavailable. {BUILD_HINT}") from exc
    LOGGER.info("Nebius sandbox backend ready (image=%s)", ref)
    return ContreeSandbox(ContreeSession(image))


_CACHED_BACKEND: Any | None = None


def get_sandbox_backend() -> Any:
    """One backend per process, shared by the agent and the sandbox tools."""
    global _CACHED_BACKEND
    if _CACHED_BACKEND is None:
        _CACHED_BACKEND = build_sandbox_backend()
    return _CACHED_BACKEND
