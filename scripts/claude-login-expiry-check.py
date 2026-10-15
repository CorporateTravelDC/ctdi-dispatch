#!/usr/bin/env python3
"""scripts/claude-login-expiry-check.py -- warn before the Claude Code login runs out (operator 2026-10-15).

Why: after the 2026-10-15 network outage the operator found his Claude login had expired and had to sign
back in to everything, while the box was already in trouble. Claude Code's login is two OAuth tokens in
~/.claude/.credentials.json (claudeAiOauth):
  - access token: about 8 h, renewed silently with the refresh token;
  - refresh token: an absolute lifetime from sign-in (28 days observed 2026-10-15). Renewing the access
    token does NOT extend it -- when it passes, every Claude Code session on this account needs /login.
This check reads ONLY claudeAiOauth.refreshTokenExpiresAt (never a token value) and sends ONE alert a day
ahead (operator 2026-10-15: "just a day before it expires"): the timer runs once a day, and the first run
with <= WARN_HOURS (default 48 h) left sends ops-health p4 -- so it always lands 24-48 h before expiry,
never later. One alert per login: the key is the login's own expiry (a field of the login itself, nothing
observed or computed), cleared when a new sign-in moves the expiry.

  claude-login-expiry-check.py            check and alert
  claude-login-expiry-check.py --status   print the expiry (UTC + ET) and hours left; never alerts
Env: CLAUDE_CREDENTIALS (default ~/.claude/.credentials.json), LOGIN_CHECK_STATE_DIR, LOGIN_WARN_HOURS,
LOGIN_CHECK_NTFY_BASE (tests point it at a dead port).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

CREDS = Path(os.environ.get("CLAUDE_CREDENTIALS", Path.home() / ".claude/.credentials.json"))
STATE_DIR = Path(os.environ.get("LOGIN_CHECK_STATE_DIR", "/var/lib/corporatetraveldc/claude-login-expiry"))
WARN_H = float(os.environ.get("LOGIN_WARN_HOURS", "48"))
ENV_FILE = Path("/etc/corporatetraveldc/dispatch.env")
SECRETS_FILE = Path("/etc/corporatetraveldc/dispatch-secrets.env")
ACCOUNT = os.environ.get("USER") or Path.home().name


def log(msg: str) -> None:
    line = f"[{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}] {msg}"
    print(line)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "check.log", "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def env_value(key: str, path: Path) -> str:
    try:
        for line in path.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def expiry() -> dt.datetime | None:
    try:
        v = json.loads(CREDS.read_text()).get("claudeAiOauth", {}).get("refreshTokenExpiresAt")
    except (OSError, ValueError):
        return None
    if not isinstance(v, (int, float)):
        return None
    return dt.datetime.fromtimestamp(v / 1000 if v > 1e12 else v, dt.timezone.utc)


def label(t: dt.datetime) -> str:
    return f"{t:%Y-%m-%d %H:%MZ} / {t.astimezone(ZoneInfo('America/New_York')):%Y-%m-%d %H:%M} ET"


def send(title: str, msg: str, priority: int) -> bool:
    base = os.environ.get("LOGIN_CHECK_NTFY_BASE") or env_value("NTFY_BASE_URL", ENV_FILE) or "http://127.0.0.1:2586"
    topic = env_value("NTFY_OPS_TOPIC", ENV_FILE) or "ops-health"
    token = env_value("NTFY_TOKEN", SECRETS_FILE).split(":", 1)[0]
    req = urllib.request.Request(f"{base}/{topic}", data=msg.encode(), method="POST",
                                 headers={"Title": title, "Priority": str(priority), "Tags": "key"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")     # in-process header, never argv
    try:
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:                                          # noqa: BLE001 -- report, never raise
        log(f"warn: ntfy send failed (token_set={'yes' if token else 'no'})")
        return False


def main(argv: list[str]) -> int:
    exp = expiry()
    if exp is None:
        log(f"no Claude login expiry found in {CREDS} (signed out, or the file format changed)")
        return 2
    left_h = (exp - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600
    if argv[:1] == ["--status"]:
        print(f"Claude login ({ACCOUNT}) expires {label(exp)}; {left_h:.1f} h left "
              f"(alerts once, at the first daily check with <= {WARN_H:g} h left)")
        return 0
    key_file = STATE_DIR / "last-alert-key"
    if left_h > WARN_H:
        if key_file.exists():
            key_file.unlink()
            log(f"new sign-in: expires {label(exp)} (alert state cleared)")
        return 0
    key = str(int(exp.timestamp()))
    try:
        if key_file.read_text().strip() == key:
            return 0
    except OSError:
        pass
    stage = "expired" if left_h <= 0 else "warn"
    title = f"Claude login {'EXPIRED' if stage == 'expired' else f'expires in {left_h:.0f} h'} ({ACCOUNT})"
    msg = (f"The Claude Code login for {ACCOUNT} {'expired' if stage == 'expired' else 'expires'} {label(exp)}. "
           f"Sign in again (/login in any Claude Code session) so sessions, agents and connectors don't drop at once.")
    log(f"{stage}: expires {label(exp)}, {left_h:.1f} h left")
    if send(title, msg, 4):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
