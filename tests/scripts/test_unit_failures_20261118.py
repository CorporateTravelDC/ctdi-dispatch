"""2026-11-18 failed-unit fixes: the backup stage guard, RSS redirects, the weekly-dump classification and
early exit, and the static wiring. Synthetic inputs only; no network, no real disk filling."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts/backup/stage_guard.py"
BACKUP = ROOT / "scripts/backup/ctdi-backup.sh"
DUMP = ROOT / "scripts/second-brain-weekly-dump.sh"


def run_guard(*args, stdin=b""):
    return subprocess.run([sys.executable, str(GUARD), *args], input=stdin, capture_output=True, timeout=30)


def test_limit_passes_small_stream(tmp_path):
    out = tmp_path / "d.pgdump"
    r = run_guard("limit", str(out), "1000", stdin=b"x" * 999)
    assert r.returncode == 0 and out.read_bytes() == b"x" * 999


def test_limit_stops_and_removes_partial(tmp_path):
    out = tmp_path / "d.pgdump"
    r = run_guard("limit", str(out), "1000", stdin=b"x" * 5000)
    assert r.returncode == 75 and not out.exists()
    assert b"passed its" in r.stderr


def test_preflight_ok_and_short(tmp_path):
    assert run_guard("preflight", str(tmp_path), "1", "0").returncode == 0
    r = run_guard("preflight", str(tmp_path), str(1 << 62), "0")
    assert r.returncode == 75 and b"NOT ENOUGH SPACE" in r.stderr and b"short by" in r.stderr


def test_backup_streams_every_dump_through_the_guard():
    s = BACKUP.read_text()
    assert '| python3 "$STAGE_GUARD" limit "$out" "$room"' in s
    assert 'python3 "$STAGE_GUARD" preflight "$WORK" "$need" "$MIN_FREE"' in s
    assert '> "$out" </dev/null' not in s                       # no unguarded redirect left
    # the preflight runs before the heavy-work wait and before any dump; --preflight-only stops after it
    assert s.index('log "preflight: stage space"') < s.index('heavy_lock_acquire "nightly backup"') < s.index('phase "1/4 dump: dispatch Postgres"')
    assert "--preflight-only) PRE=1" in s and 'if [[ "$PRE" == 1 ]]' in s
    assert "BACKUP_COMPRESS_TABLES" in s and "--exclude-table-data=%s" in s


def test_backup_script_parses():
    assert subprocess.run(["bash", "-n", str(BACKUP)], timeout=30).returncode == 0
    assert subprocess.run(["bash", "-n", str(DUMP)], timeout=30).returncode == 0


def _fake_tool(d: Path, name: str, body: str) -> None:
    p = d / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def test_weekly_dump_classifies_auth_failure_without_printing_output(tmp_path):
    s = DUMP.read_text()
    assert "class=${CLASS}" in s and "provider-auth" in s and "provider-billing-or-limit" in s
    assert 'echo "second-brain-weekly-dump: claude exit ${RC}; class=${CLASS}; full output in ${LOG}" >&2' in s
    # the journal line never carries ${OUTPUT}
    line = [l for l in s.splitlines() if "class=${CLASS}" in l][0]
    assert "OUTPUT" not in line


def test_weekly_dump_skips_empty_memory_before_any_model_run():
    s = DUMP.read_text()
    i_skip = s.index("nothing to consolidate")
    i_run = s.index('OUTPUT="$(nohup')
    assert i_skip < i_run
    assert "corporatetraveldc/01-Sources/manual/PLATFORM-RULES-CANONICAL.md" in s


class _Resp:
    def __init__(self, code, loc=None, content=b"<rss/>"):
        self.status_code = code
        self.headers = {"location": loc} if loc else {}
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture()
def rss(monkeypatch):
    sys.path.insert(0, str(ROOT / "src"))
    monkeypatch.setenv("NEXTCLOUD_ADMIN_USER", "test-placeholder")   # import-time guard only; no request is made
    import importlib
    try:
        mod = importlib.import_module("poller.skills.second_brain_rss")
    except Exception as e:  # noqa: BLE001 -- the skill's imports need the second_brain env
        pytest.skip(f"second_brain_rss not importable here: {e}")
    return mod


def test_rss_follows_https_redirects(rss, monkeypatch):
    seq = {"https://a.example/feed": _Resp(301, "https://a.example/feed/"),
           "https://a.example/feed/": _Resp(302, "/stories.rss"),
           "https://a.example/stories.rss": _Resp(200)}
    monkeypatch.setattr(rss.httpx, "get", lambda url, **kw: seq[str(url)])
    assert rss._fetch("https://a.example/feed").status_code == 200


def test_rss_refuses_downgrade_and_loops(rss, monkeypatch):
    monkeypatch.setattr(rss.httpx, "get", lambda url, **kw: _Resp(301, "http://a.example/feed"))
    with pytest.raises(rss.httpx.HTTPError):
        rss._fetch("https://a.example/feed")
    monkeypatch.setattr(rss.httpx, "get", lambda url, **kw: _Resp(302, str(url) + "x"))
    with pytest.raises(rss.httpx.HTTPError):
        rss._fetch("https://a.example/f")


def test_rss_catalog_moved_feeds():
    s = (ROOT / "src/shared/rss_catalog.py").read_text()
    assert "https://www.bairdmaritime.com/stories.rss" in s and "https://tradeonlytoday.com/feed/" in s


def test_stack_refresh_marks_retired_team_accounts():
    s = (ROOT / "scripts/stack-refresh.sh").read_text()
    assert "team_key_line()" in s and "retired" in s
    assert "fp_team_keys_raw() { for a in $(team_accounts); do team_key_line" in s
