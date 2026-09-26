"""`state/health.json` — one line per scheduled job, in the fleet's health/v1.

Basics in Order §1.1 (Ishay, 26.09.2026, via Boss under Mandate 1): every
scheduled job writes its own line at the end of every run, and Boss's
morning report reads only this file. Schema: `~/boss/projects/basics-in-order.md`.

Rules this module holds so callers cannot get them wrong:
- read-merge-write, atomic (tmp + rename): two jobs finishing together
  must not erase each other's line;
- a job reports what actually happened — `failed` even when the process
  exits 0 (systemd's "success" is exactly the lie this file exists to
  catch);
- names only, never a secret value.

Standard library only, on purpose: `backup_doctor.py` runs under the
system python and `auto_push.sh` from a shell, and both write here too.

CLI, for the shell jobs:
    python3 -m grocery_bot.health <job> <ok|failed|skipped-by-design> [detail]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
HEALTH_PATH = ROOT / "state" / "health.json"
DB_PATH = ROOT / "data" / "grocery_bot.sqlite3"
SESSIONS = ROOT / "data" / "sessions"
ISRAEL = ZoneInfo("Asia/Jerusalem")

STATUSES = ("ok", "failed", "skipped-by-design")

# health/v1 rule 6 (Boss, 26.09): the report calls an entry stale only
# after 2x this; without it, after 48 h. Values are the real schedules
# (systemd timers, the bot's job_queue); event-driven sources — carts and
# orders move only when the household shops — get two weeks.
EXPECTED_EVERY_H = {
    "grocery-prices": 12,          # 06/12/18:15 — the longest gap is overnight
    "grocery-backup": 0.5,
    "grocery-doctor": 1,
    "grocery-bot:watch_list": 0.05,
    "grocery-bot:drain_deferred_cycle": 0.04,
    "grocery-bot:cadence_check": 24,
    "grocery-bot:nightly_learn": 24,
    "grocery-bot:resume_runs": 720,  # once per bot start, not periodic
    "gordonchrome": 1,  # probed on every write (every ~3 min while the bot runs)
}
_EXPECTED_BY_SUFFIX = {"-orders": 336, "-cart": 336, "-feed": 24}


def _expected(key: str) -> float | None:
    if key in EXPECTED_EVERY_H:
        return EXPECTED_EVERY_H[key]
    for suffix, hours in _EXPECTED_BY_SUFFIX.items():
        if key.endswith(suffix):
            return hours
    return None


def now() -> str:
    return datetime.now(ISRAEL).replace(microsecond=0).isoformat()


def _iso(value) -> str | None:
    """Any stored timestamp as ISO-8601 with an offset, or None."""
    if not value:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Bare dates/datetimes in this DB are Israel-local (order_log,
        # feed dates); run_items and app_state carry their own offset.
        parsed = parsed.replace(tzinfo=ISRAEL)
    return parsed.astimezone(ISRAEL).replace(microsecond=0).isoformat()


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".health-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lock(path: Path):
    """An advisory lock around read-merge-write; POSIX only, which this is."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path.parent / ".health.lock", "w")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def derived(db_path: Path = DB_PATH, sessions: Path = SESSIONS) -> tuple[dict, dict]:
    """Sources and expiries that are cheap to read on every write.

    The price feeds are not here — that query takes ~5 s over a million
    rows, so only the prices job itself reports them.
    """
    sources: dict = {}
    expiries: dict = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            for store, last in conn.execute(
                "SELECT store, MAX(attempted_at) FROM run_items "
                "WHERE outcome = 'verified' GROUP BY store"
            ):
                sources[f"{store}-cart"] = {"last_data_at": _iso(last), "status": "ok"}
            for store, last in conn.execute(
                "SELECT store, MAX(placed_at) FROM order_log GROUP BY store"
            ):
                sources[f"{store}-orders"] = {"last_data_at": _iso(last), "status": "ok"}
            tivtaam_ok = dict(conn.execute(
                "SELECT store, MAX(attempted_at) FROM run_items "
                "WHERE outcome = 'verified' AND store = 'tivtaam'"
            ).fetchall()).get("tivtaam")
        finally:
            conn.close()
    except sqlite3.Error:
        tivtaam_ok = None

    expiries["tivtaam-session"] = {
        "expires_at": None,
        "last_verified_at": _iso(tivtaam_ok),
        "kind": "session",
        "note": "GordonChrome profile on liran-aba-pc (CDP 9224); the site does not "
                "expose an expiry — last_verified_at is the last verified cart add",
    }

    seen = _load(sessions / "behatsdaa_login_seen.json").get("seen_at")
    state = _load(sessions / "behatsdaa_state.json")
    names = {c.get("name") for c in state.get("cookies") or []}
    live = bool(names & {"AccessToken", ".AspNetCore.Session"})
    expiries["behatsdaa"] = {
        "expires_at": None,
        "last_verified_at": _iso(seen),
        "kind": "session",
        "note": ("session cookies present; JWT lasts ~30 min" if live else
                 "expired: no AccessToken/.AspNetCore.Session in saved state — needs "
                 "Ishay's login on noVNC (HANDOFF §3)"),
    }
    return sources, expiries


def browser_probe(timeout: float = 3.0) -> tuple[str, dict] | None:
    """("gordonchrome", job line) from the same check the bot runs before a cart run.

    Boss's report sees only MiriChrome through Fleet, so GordonChrome's
    state lives here (Boss, 26.09). It is the bot's own question —
    `browser.cdp_reachable`, restated in the standard library because
    the doctor runs under the system python — so red here means exactly
    "Tiv Taam is falling back to the local browser". None when no remote
    browser is configured.
    """
    import urllib.request

    url = (os.environ.get("GORDON_BROWSER_CDP_URL") or "").strip()
    if not url:
        return None
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        up = bool(payload.get("Browser") or payload.get("webSocketDebuggerUrl"))
    except Exception:  # noqa: BLE001 - unreachable is the answer, not an error
        up = False
    stores = os.environ.get("GORDON_BROWSER_CDP_STORES", "")
    line = {"status": "ok" if up else "failed", "last_run": now()}
    if not up:
        line["detail"] = (f"GordonChrome {url} not answering; {stores or 'its stores'} fall back to the "
                          "local browser + exit node. Down since the 24.09 17:40 reboot of liran-aba-pc "
                          "(Bob: no chrome.exe running)")
        line["owner_action"] = "someone at liran-aba-pc restarts GordonChrome; ownership is Ishay's call"
    return "gordonchrome", line


def update(job: str, status: str, detail: str = "", owner_action: str = "",
           sources: dict | None = None, expiries: dict | None = None,
           path: Path | None = None, include_derived: bool = True) -> dict:
    """Write this job's line (and anything it knows) into the file."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    if status != "ok" and not detail:
        detail = "no detail given"
    path = path or HEALTH_PATH
    extra_sources, extra_expiries = derived() if include_derived else ({}, {})
    probe = browser_probe() if include_derived else None
    lock = _lock(path)
    try:
        data = _load(path)
        data["schema"] = "health/v1"
        data["agent"] = "gordon"
        data["written_at"] = now()
        line = {"status": status, "last_run": now()}
        if detail:
            line["detail"] = " ".join(str(detail).split())[:300]
        if owner_action:
            line["owner_action"] = owner_action
        data.setdefault("jobs", {})[job] = line
        if probe:
            data["jobs"][probe[0]] = probe[1]
        data.setdefault("sources", {}).update(extra_sources)
        data["sources"].update(sources or {})
        data.setdefault("expiries", {}).update(extra_expiries)
        data["expiries"].update(expiries or {})
        for section in ("jobs", "sources"):
            for key, entry in data[section].items():
                hours = _expected(key)
                if hours is not None and isinstance(entry, dict):
                    entry["expected_every_h"] = hours
        _write(path, data)
        return data
    finally:
        lock.close()


def safe_update(*args, **kwargs) -> None:
    """`update`, but never the reason a job fails."""
    try:
        update(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - health must not take a job down
        print(f"health.json write failed: {exc}", file=sys.stderr)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in STATUSES:
        print(__doc__, file=sys.stderr)
        return 2
    update(argv[0], argv[1], " ".join(argv[2:]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
