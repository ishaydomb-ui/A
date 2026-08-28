"""Configuration loaded from environment variables.

No secrets are hardcoded anywhere in this project. Everything here is read
from the environment at startup; see .env.example for the full list.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    allowed_telegram_user_ids: list[int]
    db_path: str
    shufersal_storage_state_path: str
    tivtaam_storage_state_path: str
    enabled_stores: list[str]
    headless: bool = True
    # Which branch the public price/promotion feed is read for. Prices and
    # promotions are per-branch, so this has to name a real store id from
    # the dropdown at prices.shufersal.co.il.
    shufersal_price_store_id: str = "9"

    @staticmethod
    def from_env() -> "Config":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set. Create a bot via @BotFather and "
                "set it in the environment (see .env.example)."
            )
        allowed_ids_raw = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "")
        allowed_ids = [int(v) for v in _split_csv(allowed_ids_raw)] if allowed_ids_raw else []
        return Config(
            telegram_bot_token=token,
            allowed_telegram_user_ids=allowed_ids,
            db_path=os.environ.get("GROCERY_BOT_DB_PATH", "data/grocery_bot.sqlite3"),
            shufersal_storage_state_path=os.environ.get(
                "SHUFERSAL_STORAGE_STATE_PATH", "data/sessions/shufersal_storage_state.json"
            ),
            tivtaam_storage_state_path=os.environ.get(
                "TIVTAAM_STORAGE_STATE_PATH", "data/sessions/tivtaam_storage_state.json"
            ),
            enabled_stores=_split_csv(os.environ.get("ENABLED_STORES", "shufersal")),
            headless=os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false",
            shufersal_price_store_id=os.environ.get("SHUFERSAL_PRICE_STORE_ID", "9"),
        )
