"""The sandbox image contract shared by the agent backend and phone planner.

The image is not a generic base: it carries the two toolchains OYI's sandbox
work actually needs. Keep the default in one place so the agent shell and the
phone-use planner cannot drift onto different images.
"""

from __future__ import annotations

import os

DEFAULT_IMAGE = "oyi-sandbox:latest"

BUILD_HINT = (
    "Build it with tools/build_sandbox_image.sh "
    "(source: sandbox_image/Dockerfile)."
)


def image_ref() -> str:
    """The image tag to use, from OYI_SANDBOX_IMAGE or the built default."""
    return (os.getenv("OYI_SANDBOX_IMAGE") or DEFAULT_IMAGE).strip() or DEFAULT_IMAGE
