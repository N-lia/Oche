"""Diagram generation tools for the OYI agent.

The model reaches for :func:`render_diagram` when a reply needs a visual
(flowchart, sequence diagram, state machine, ...). The tool renders Mermaid
source into SVG through the Nebius token sandbox and returns the result so the
model can present a diagram it knows is valid.

Rendering pipeline
------------------
1. Nebius token sandbox (``NEBIUS_SANDBOX_URL``): POST the Mermaid source to
   the sandbox, get SVG back. This is the path the user asked for — diagram
   generation happens on Nebius, not in the model's head and not client-side.
2. No local fallback is bundled (no Node/mermaid-cli in this environment); if
   the sandbox is unreachable the tool returns a clear error so the model can
   say so instead of fabricating an image.

The caller (model) is responsible for emitting a
``<pre><code class="language-mermaid">...</code></pre> block with the source so
the miniapp renders it via pele; the tool's SVG is the server-side generated
artifact and a proof the diagram is valid.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("oyi.diagram")

_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=120),
        )
    return _HTTP_CLIENT


def get_sandbox_url() -> str:
    """Nebius token sandbox endpoint for diagram rendering."""
    import os

    return (os.environ.get("NEBIUS_SANDBOX_URL") or "").strip()


class RenderDiagramInput(BaseModel):
    mermaid_source: str = Field(
        ...,
        description=(
            "Valid Mermaid diagram source (flowchart / sequenceDiagram / "
            "stateDiagram / classDiagram / erDiagram / ...). Keep to core "
            "syntax, 6-14 named nodes, short labels. No styling directives."
        ),
    )
    format: str = Field(
        "svg",
        description="Output format. Only 'svg' is supported.",
    )


def _extract_svg(payload: Any) -> str | None:
    """Pull an SVG string out of common sandbox response shapes."""
    if not isinstance(payload, dict):
        return None
    for key in ("svg", "image_svg", "svg_xml", "document"):
        val = payload.get(key)
        if isinstance(val, str) and val.lstrip().startswith("<svg"):
            return val
    # Some sandboxes return a public URL instead of inline SVG.
    for key in ("url", "image_url", "svg_url"):
        val = payload.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    return None


async def _render_via_sandbox(mermaid_source: str) -> dict[str, Any]:
    """Render Mermaid to SVG through the Nebius token sandbox."""
    import os

    url = get_sandbox_url()
    if not url:
        return {
            "ok": False,
            "svg": None,
            "source": mermaid_source,
            "error": (
                "NEBIUS_SANDBOX_URL is not configured. Set it to the Nebius "
                "token sandbox diagram-rendering endpoint and retry."
            ),
        }

    target_url = url.rstrip("/")
    api_key = os.environ.get("NEBIUS_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    client = _get_http_client()
    try:
        response = await client.post(
            target_url,
            json={"mermaid": mermaid_source, "format": "svg"},
            headers=headers,
        )
    except httpx.HTTPError as exc:
        LOGGER.error("Nebius sandbox diagram request failed: %s", exc)
        return {
            "ok": False,
            "svg": None,
            "source": mermaid_source,
            "error": f"Nebius sandbox request failed: {exc}",
        }

    if response.status_code != 200:
        LOGGER.error(
            "Nebius sandbox diagram render returned %s: %s",
            response.status_code,
            response.text,
        )
        return {
            "ok": False,
            "svg": None,
            "source": mermaid_source,
            "error": (
                f"Nebius sandbox returned HTTP {response.status_code}. "
                f"{response.text[:200]}"
            ),
        }

    try:
        payload = response.json()
    except ValueError:
        payload = {"svg": response.text}

    svg = _extract_svg(payload)
    if not svg:
        return {
            "ok": False,
            "svg": None,
            "source": mermaid_source,
            "error": (
                "Nebius sandbox returned a response but no SVG was found in it "
                f"(keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__})."
            ),
        }

    LOGGER.info("Diagram rendered via Nebius sandbox (svg_len=%d)", len(svg))
    return {"ok": True, "svg": svg, "source": mermaid_source, "error": None}


@tool("render_diagram", args_schema=RenderDiagramInput)
async def render_diagram(mermaid_source: str, format: str = "svg") -> str:
    """Render a Mermaid diagram into SVG via the Nebius token sandbox.

    Call this when the reply needs a visual (flowchart, sequence, state,
    class diagram). Pass valid Mermaid source; the sandbox renders it and
    returns SVG. Then present the diagram to Sir Mario — in the miniapp emit a
    ``<pre><code class="language-mermaid">...</code></pre> block with the source
    so pele renders it live, and you may note that it was generated on Nebius.

    Only use this for genuine diagrams. Do not call it for simple linear
    flows (bullets suffice) or purely conversational replies.
    """
    result = await _render_via_sandbox(mermaid_source)
    if not result["ok"]:
        return f"Diagram generation failed: {result['error']}"
    svg_len = len(result["svg"]) if result["svg"] else 0
    return (
        "Diagram rendered successfully via Nebius sandbox "
        f"(SVG {svg_len} bytes). Mermaid source:\n"
        f"<pre><code class=\"language-mermaid\">{mermaid_source}</code></pre>"
    )


DIAGRAM_TOOLS: list[BaseTool] = [render_diagram]
