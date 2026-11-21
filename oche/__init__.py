"""Oche: a tiny Cordis-style plugin core around a LangChain Deep Agent.

    from oche import create_app
    app = create_app()          # loads the plugins listed in oche.toml
    await app.start()
    reply = await app.agent.achat("hello", thread_id="me")
"""

from oche.core import App, Context, Scope
from oche.loader import create_app, load_config

__all__ = ["App", "Context", "Scope", "create_app", "load_config"]
