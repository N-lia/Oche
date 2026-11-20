"""Oche: a tiny plugin core (Cordis-style) with a pi-style agent loop.

    from oche import App
    app = App()
    app.plugin(my_plugin, {"option": 1})
    await app.start()
"""

from oche.core import App, Context, Scope, Tool, schema_from_signature
from oche.loader import create_app, load_config

__all__ = ["App", "Context", "Scope", "Tool", "create_app", "load_config", "schema_from_signature"]
