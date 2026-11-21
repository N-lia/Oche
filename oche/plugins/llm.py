"""Chat models for an OpenAI-compatible endpoint (Nebius by default), as the ``llm`` service.

``ctx.llm.get(model_name)`` returns a cached LangChain ``ChatOpenAI``. Models
not listed in ``thinking`` get ``enable_thinking: false`` so the latency-bound
tiers start answering immediately.
"""

from __future__ import annotations

import os
from typing import Any

from oche.core import Context

name = "llm"


class Models:
    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url = config.get("base_url", "https://api.tokenfactory.nebius.com/v1/")
        self.api_key_env = config.get("api_key_env", "NEBIUS_API_KEY")
        self.default = config.get("model", "")
        self.thinking = set(config.get("thinking", ()))
        self.read_timeout = float(config.get("read_timeout", 45.0))
        self._cache: dict[tuple[str, tuple], Any] = {}

    def get(self, model_name: str | None = None, **overrides: Any) -> Any:
        import httpx
        from langchain_openai import ChatOpenAI

        model_name = model_name or self.default
        key = (model_name, tuple(sorted(overrides.items())))
        if key not in self._cache:
            kwargs: dict[str, Any] = {}
            if model_name not in self.thinking:
                kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
            kwargs.update(overrides)
            self._cache[key] = ChatOpenAI(
                model=model_name,
                base_url=self.base_url,
                api_key=os.environ[self.api_key_env],
                streaming=True,
                # Bounded sockets: a stalled connection must not freeze a live voice turn.
                timeout=httpx.Timeout(connect=10.0, read=self.read_timeout, write=10.0, pool=10.0),
                **kwargs,
            )
        return self._cache[key]


def apply(ctx: Context, config: dict[str, Any]) -> None:
    ctx.provide("llm", Models(config))
