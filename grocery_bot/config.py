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
    # Both chains block non-Israeli IPs, and this server is in France, so
    # store traffic has to leave through an Israeli exit. This is a local
    # SOCKS5 port (Tailscale in userspace mode, exiting via a device at
    # home) rather than a system-wide route on purpose: other projects'
    # bots share this machine and must keep the normal connection.
    playwright_proxy: str = ""
    # Which branch the public price/promotion feed is read for. Prices and
    # promotions are per-branch, so this has to name a real store id from
    # the dropdown at prices.shufersal.co.il.
    shufersal_price_store_id: str = "9"
    # Store credentials, used only to re-create an expired session without
    # interrupting the user (the project's "minimum user dependency" rule).
    # Optional: with them unset the bot still runs, it just can't recover
    # on its own once the saved session expires.
    bot_username: str = ""
    shufersal_username: str = ""
    shufersal_password: str = ""
    # Where shufersal_username/password actually came from -- "bitwarden"
    # or "env" -- never the values themselves. Surfaced so a log line can
    # say which channel is live without touching the secret.
    shufersal_credential_source: str = "env"
    # Whether a cycle may put exceptional promotions into the cart by
    # itself, on top of what was actually asked for (see dealfill.py).
    # On by default, set by Ishay 2026-09-06: deleting a line he doesn't
    # want costs seconds, and a deal that only arrives as a message costs
    # a second action that measurably does not happen.
    auto_add_deals: bool = True

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
        shufersal_username, shufersal_password, shufersal_credential_source = _shufersal_credentials()
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
            playwright_proxy=os.environ.get("PLAYWRIGHT_PROXY", ""),
            bot_username=os.environ.get("TELEGRAM_BOT_USERNAME", ""),
            shufersal_username=shufersal_username,
            shufersal_password=shufersal_password,
            shufersal_credential_source=shufersal_credential_source,
            auto_add_deals=os.environ.get("AUTO_ADD_DEALS", "true").lower() != "false",
        )


def _shufersal_credentials() -> tuple[str, str, str]:
    """Bitwarden first, plain env vars as fallback.

    Primary channel per Ishay 2026-09-18 (see grocery_bot/bitwarden.py).
    Falls back whenever the vault is unavailable (no `bw`, no BW_* vars --
    the common case on a box without ~/.config/familyos/secrets.env) or
    has no matching item, so an unconfigured vault changes nothing.
    """
    from . import bitwarden, login

    if bitwarden.available():
        try:
            bw_user = bitwarden.username_for(login.LOGIN_URL)
            bw_pass = bitwarden.password_for(login.LOGIN_URL)
        except Exception:
            bw_user = bw_pass = None
        if bw_user and bw_pass:
            return bw_user, bw_pass, "bitwarden"
    return (
        os.environ.get("SHUFERSAL_USERNAME", ""),
        os.environ.get("SHUFERSAL_PASSWORD", ""),
        "env",
    )
