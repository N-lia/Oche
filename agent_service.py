"""The OYI agent service: a Deep Agent assembled from the plugins in ``oche.toml``.

Every feature (model tiers, routing, privacy, memory, web search, files,
subagents, voice, Telegram formatting) is a plugin under ``oche/plugins``.
This module only loads them and exposes the ``agent`` service under its old
name, so ``main.py``, the bot and the WebApp keep working unchanged.
"""

from __future__ import annotations

from dotenv import load_dotenv

from oche import create_app
from oche.plugins.agent import Agent

load_dotenv(override=True)

app = create_app()
agent_service: Agent = app.agent
AgentService = Agent
