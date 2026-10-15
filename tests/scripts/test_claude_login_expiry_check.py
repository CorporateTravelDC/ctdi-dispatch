"""2026-10-15: the Claude login expiry check reads only refreshTokenExpiresAt, alerts once per stage per
login, and clears when a new sign-in moves the expiry. Never the real ntfy (dead port), never a real
credentials file (a synthetic one in tmp_path, with placeholder token values)."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/claude-login-expiry-check.py"


def creds(tmp: Path, hours_left: float) -> Path:
    p = tmp / "credentials.json"
    p.write_text(json.dumps({"claudeAiOauth": {"accessToken": "placeholder-not-a-token",
                                                "refreshToken": "placeholder-not-a-token",
                                                "refreshTokenExpiresAt": int((time.time() + hours_left * 3600) * 1000)}}))
    return p


def run(tmp: Path, cred: Path, *args):
    env = dict(os.environ, CLAUDE_CREDENTIALS=str(cred), LOGIN_CHECK_STATE_DIR=str(tmp / "state"),
               LOGIN_CHECK_NTFY_BASE="http://127.0.0.1:9", PATH="/usr/bin:/bin")
    return subprocess.run(["python3", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def test_far_from_expiry_is_quiet(tmp_path):
    r = run(tmp_path, creds(tmp_path, 400))
    assert r.returncode == 0 and r.stdout == ""


def test_warn_window_tries_to_alert_and_never_prints_tokens(tmp_path):
    r = run(tmp_path, creds(tmp_path, 30))
    assert r.returncode == 0 and "warn:" in r.stdout and "ntfy send failed" in r.stdout
    assert "placeholder-not-a-token" not in r.stdout + r.stderr
    assert not (tmp_path / "state/last-alert-key").exists()          # failed send is retried next run


def test_one_day_window_and_expired(tmp_path):
    assert run(tmp_path, creds(tmp_path, 49)).stdout == ""          # outside the 48 h window: quiet
    assert "warn:" in run(tmp_path, creds(tmp_path, 47)).stdout
    assert "expired:" in run(tmp_path, creds(tmp_path, -1)).stdout


def test_status_prints_utc_and_et(tmp_path):
    r = run(tmp_path, creds(tmp_path, 100), "--status")
    assert r.returncode == 0 and "Z / " in r.stdout and " ET;" in r.stdout


def test_missing_or_signed_out(tmp_path):
    p = tmp_path / "credentials.json"
    p.write_text(json.dumps({"mcpOAuth": {}}))
    assert run(tmp_path, p).returncode == 2
    assert run(tmp_path, tmp_path / "absent.json").returncode == 2


def test_new_sign_in_clears_alert_state(tmp_path):
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state/last-alert-key").write_text("1\n")
    r = run(tmp_path, creds(tmp_path, 28 * 24))
    assert r.returncode == 0 and "alert state cleared" in r.stdout
    assert not (tmp_path / "state/last-alert-key").exists()
