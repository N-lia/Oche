"""Run Oche in the terminal: ``python -m oche [config.toml]``."""

from __future__ import annotations

import asyncio
import logging
import sys

from oche.loader import create_app, load_config


async def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.WARNING)
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    config = load_config(argv[0] if argv else None)
    plugins = config.setdefault("plugins", {})
    plugins.setdefault("cli", {})
    app = create_app(config, skip={"telegram"})
    done = asyncio.Event()
    app.on("exit", done.set)
    await app.start()
    try:
        await done.wait()
    finally:
        await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
