"""OpenAI-compatible model provider (Nebius, OpenRouter, OpenAI, vLLM, Ollama...).

Provides the ``model`` service with two methods:

- ``stream(messages, tools, model=None)`` yields ``{"type": "text", "text"}``
  and complete ``{"type": "tool_call", "id", "name", "arguments"}`` events;
- ``complete(messages, model=None, **body)`` returns the reply text.

Config: ``base_url``, ``api_key`` (or ``api_key_env``), ``model``,
``no_thinking`` (list of models that get ``enable_thinking: false``),
``extra_body`` and ``timeout``.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpx

from oche.core import Context

name = "openai"


def parse_sse(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    """Turn ``data: {...}`` lines into chunk dicts; stops at ``[DONE]``."""
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        if data:
            yield json.loads(data)


class ToolCallBuffer:
    """Reassemble streamed tool-call fragments by index."""

    def __init__(self) -> None:
        self.calls: dict[int, dict[str, str]] = {}

    def add(self, deltas: list[dict[str, Any]]) -> None:
        for delta in deltas:
            slot = self.calls.setdefault(delta.get("index", len(self.calls)), {"id": "", "name": "", "arguments": ""})
            if delta.get("id"):
                slot["id"] = delta["id"]
            function = delta.get("function") or {}
            if function.get("name"):
                slot["name"] += function["name"]
            if function.get("arguments"):
                slot["arguments"] += function["arguments"]

    def drain(self) -> list[dict[str, Any]]:
        out = [
            {"type": "tool_call", "id": c["id"] or f"call_{i}", "name": c["name"], "arguments": c["arguments"] or "{}"}
            for i, c in sorted(self.calls.items())
        ]
        self.calls.clear()
        return out


class OpenAIModel:
    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url = str(config.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = config.get("api_key") or os.environ.get(config.get("api_key_env", "OPENAI_API_KEY"), "")
        self.default_model = config.get("model", "")
        self.no_thinking = set(config.get("no_thinking", ()))
        self.extra_body = dict(config.get("extra_body", {}))
        self.timeout = httpx.Timeout(
            connect=10.0, read=float(config.get("timeout", 45.0)), write=10.0, pool=10.0
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _body(self, messages: list[dict[str, Any]], model: str | None, **extra: Any) -> dict[str, Any]:
        model = model or self.default_model
        body: dict[str, Any] = {"model": model, "messages": messages, **self.extra_body, **extra}
        if model in self.no_thinking:
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
        return body

    async def stream(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, model: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        body = self._body(messages, model, stream=True)
        if tools:
            body["tools"] = tools
        buffer = ToolCallBuffer()
        async with self.client.stream("POST", "/chat/completions", json=body) as response:
            if response.status_code >= 400:
                detail = (await response.aread()).decode("utf-8", "replace")[:500]
                raise RuntimeError(f"model request failed ({response.status_code}): {detail}")
            async for line in response.aiter_lines():
                for chunk in parse_sse([line]):
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield {"type": "text", "text": delta["content"]}
                        if delta.get("tool_calls"):
                            buffer.add(delta["tool_calls"])
        for call in buffer.drain():
            yield call

    async def complete(self, messages: list[dict[str, Any]], model: str | None = None, **extra: Any) -> str:
        response = await self.client.post("/chat/completions", json=self._body(messages, model, **extra))
        response.raise_for_status()
        choices = response.json().get("choices") or [{}]
        return (choices[0].get("message") or {}).get("content") or ""


def apply(ctx: Context, config: dict[str, Any]) -> None:
    model = OpenAIModel(config)
    ctx.provide(config.get("service", "model"), model)
    ctx.effect(model.close)
