"""Store credentials from the household's shared Bitwarden vault.

Primary credential channel as of 2026-09-18, per Ishay (relayed via Miri,
quoted verbatim in the family-runtime spec discussion): "אני רוצה שזה יהיה
המסלול הראשי כשיש צורך להכנס לאתר... שיחליפו ויעדכנו כל פרוסס שסותר את
זה." Config.from_env() tries this first for SHUFERSAL_USERNAME/PASSWORD
and falls back to the plain env vars when it is unavailable or the vault
has no matching item -- so an account with no Bitwarden entry, or a box
where `bw` isn't configured, behaves exactly as before.

This is a deliberate near-copy of ~/familyos/actions/bitwarden.py (commit
57ea227), not a reimplementation: same `bw` CLI, same env var names
(BW_CLIENTID/BW_CLIENTSECRET/BW_PASSWORD), same operational lessons --
sync-before-read (a password added on Ishay's phone is invisible here
until a sync) and retry-once-on-failed-listing (the cached session key
dies the moment anything else unlocks the same account, and Gordon and
Nigel now share this vault). Kept separate from that module rather than
imported from it because ~/familyos is a different project/repo Gordon
has no business importing across.

Same boundary as the reference: this is the only place in Gordon that
talks to `bw`. A password is read here and handed directly to the
caller (login.py, which types it into the page); it must never be
logged, put in a Telegram message, or written to storage.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

_SESSION = {"key": None, "at": 0.0, "synced": 0.0}
SESSION_TTL_S = 20 * 60
SYNC_EVERY_S = 5 * 60


def _bw() -> str:
    return shutil.which("bw") or os.path.expanduser("~/bin/bw")


def available() -> bool:
    return bool(
        os.path.exists(_bw())
        and os.environ.get("BW_CLIENTID")
        and os.environ.get("BW_CLIENTSECRET")
        and os.environ.get("BW_PASSWORD")
    )


def _run(args, timeout: int = 40, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "BW_NOINTERACTION": "true", **(extra_env or {})}
    return subprocess.run([_bw(), *args], capture_output=True, text=True, timeout=timeout, env=env)


def _session() -> str:
    if _SESSION["key"] and time.time() - _SESSION["at"] < SESSION_TTL_S:
        return _SESSION["key"]
    st = _run(["status"])
    try:
        status = json.loads(st.stdout or "{}").get("status")
    except ValueError:
        status = None
    if status == "unauthenticated":
        r = _run(["login", "--apikey"])
        if r.returncode != 0:
            raise RuntimeError("bw login failed")
    r = _run(["unlock", "--passwordenv", "BW_PASSWORD", "--raw"])
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError("bw unlock failed")
    _SESSION.update(key=r.stdout.strip(), at=time.time())
    _sync()
    return _SESSION["key"]


def _sync(force: bool = False) -> None:
    if not force and time.time() - _SESSION.get("synced", 0) < SYNC_EVERY_S:
        return
    try:
        _run(["sync"], extra_env={"BW_SESSION": _SESSION["key"]})
        _SESSION["synced"] = time.time()
    except Exception:
        pass


def _items(url: str, _retry: bool = True) -> list:
    key = _session()
    _sync()
    r = _run(["list", "items", "--url", url], extra_env={"BW_SESSION": key})
    if r.returncode != 0:
        if _retry:
            _SESSION.update(key=None, at=0.0)
            return _items(url, _retry=False)
        return []
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        return []


def _items_by_name(name: str, _retry: bool = True) -> list:
    """Items whose name matches, for vault entries saved without a URI.

    The household's "טיב טעם" item has no URI (2026-09-21), so `--url`
    cannot find it; `--search` matches on the name.
    """
    key = _session()
    _sync()
    r = _run(["list", "items", "--search", name], extra_env={"BW_SESSION": key})
    if r.returncode != 0:
        if _retry:
            _SESSION.update(key=None, at=0.0)
            return _items_by_name(name, _retry=False)
        return []
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        return []


def _lookup(url: str, name: str | None, field: str) -> str | None:
    if not available() or not (url or name):
        return None
    try:
        items = _items(url) if url else []
        if not any((it.get("login") or {}).get(field) for it in items) and name:
            items = _items_by_name(name)
    except Exception:
        return None
    for it in items:
        value = (it.get("login") or {}).get(field)
        if value:
            return value
    return None


def password_for(url: str, name: str | None = None) -> str | None:
    """The password for `url`, falling back to an item named `name`."""
    return _lookup(url, name, "password")


def username_for(url: str, name: str | None = None) -> str | None:
    return _lookup(url, name, "username")
