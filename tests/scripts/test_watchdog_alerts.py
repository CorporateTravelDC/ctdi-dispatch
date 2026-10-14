"""2026-10-14: root watchdog alerts. Sends carry the publish-only root-alerts token (before: every
send 403'd, hidden by `|| true`); a repeating alert goes out once per distinct problem (key = alert
title + issue names WITHOUT ages); stale REST fallback feeds are ignored while their push feed is
current; the missing dns-stack-ready.target is no longer a permanent warning."""
import json
import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/watchdog.sh"


def bash(tmp_path, body, token="tk_TESTTOKEN123"):
    lib = tmp_path / "wd.sh"
    lines = SCRIPT.read_text().splitlines()
    assert lines[-1].strip() == 'main "$@"'
    lib.write_text("\n".join(lines[:-1]) + "\n")
    tokf = tmp_path / "tok"; tokf.write_text(token + "\n")
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    # fake curl: record argv and any @file header contents, answer 200
    (bindir / "curl").write_text('#!/bin/bash\necho "ARGV $*" >> "$CURL_LOG"\n'
                                 'for a in "$@"; do [[ $a == @/dev/fd/* ]] && echo "HDR $(cat "${a#@}")" >> "$CURL_LOG"; done\n'
                                 'printf 200\n')
    (bindir / "logger").write_text("#!/bin/bash\nexit 0\n")
    for f in ("curl", "logger"):
        (bindir / f).chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", CURL_LOG=str(tmp_path / "curl.log"),
               NTFY_ROOT_TOKEN_FILE=str(tokf), ALERT_STATE_FILE=str(tmp_path / "last"),
               LOG_DIR=str(tmp_path), LOG_FILE=str(tmp_path / "wd.log"))
    r = subprocess.run(["bash", "-c", f"source {lib} >/dev/null 2>&1; LOG_DIR={tmp_path}; LOG_FILE={tmp_path}/wd.log; {body}"],
                       env=env, capture_output=True, text=True, timeout=60)
    log = (tmp_path / "curl.log").read_text() if (tmp_path / "curl.log").exists() else ""
    return r, log


def test_send_carries_the_token_via_fd_never_on_argv(tmp_path):
    r, log = bash(tmp_path, 'ntfy_send ops-health "T" "m" 3')
    assert "HDR Authorization: Bearer tk_TESTTOKEN123" in log
    assert all("tk_TESTTOKEN123" not in l for l in log.splitlines() if l.startswith("ARGV"))


def test_repeating_alert_goes_once_per_distinct_problem(tmp_path):
    body = ('ISSUES=("feed_stale_crit:nws:100min" "dns_resolver_no_answer"); ntfy_once ops-health "Watchdog -- Warning" "a" 2; '
            'ISSUES=("feed_stale_crit:nws:101min" "dns_resolver_no_answer"); ntfy_once ops-health "Watchdog -- Warning" "b" 2; '
            'ISSUES=("feed_stale_crit:nws:102min"); ntfy_once ops-health "Watchdog -- Warning" "c" 2')
    r, log = bash(tmp_path, body)
    assert len([l for l in log.splitlines() if l.startswith("ARGV")]) == 2      # ages alone never re-send


def test_push_backed_rest_feed_is_not_stale_while_push_is_current():
    src = SCRIPT.read_text()
    py = src.split("stale_out=$(python3 - \"${tmpf}\" <<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
    now = time.time()
    feeds = {"feeds": [{"name": "nws", "fetched_at": now - 600000}, {"name": "push:nws", "fetched_at": now - 10},
                       {"name": "notam", "fetched_at": now - 90000}, {"name": "push:fns", "fetched_at": now - 4000},
                       {"name": "metar", "fetched_at": now - 5000}]}
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(feeds, f)
    out = subprocess.run(["python3", "-", f.name], input=py, capture_output=True, text=True).stdout
    names = {l.split(":")[1] for l in out.splitlines()}
    assert "nws" not in names                          # push current -> REST staleness is by design
    assert "notam" in names and "metar" in names       # push stale too / not push-backed -> still flagged
