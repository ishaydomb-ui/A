"""Entrypoint: wire config + storage + Telegram bot and start polling.

Run with: python -m grocery_bot.main
"""
from __future__ import annotations

import logging

from .config import Config
from .storage import Storage
from .telegram_bot import build_application


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    storage = Storage(config.db_path)
    application = build_application(config, storage)
    logging.getLogger(__name__).info("Grocery bot starting, enabled stores: %s", config.enabled_stores)
    application.run_polling()


if __name__ == "__main__":
    main()
