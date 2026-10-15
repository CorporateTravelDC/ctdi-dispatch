"""2026-10-15: the healthchecks.io dead-man ping. Never sends anything in tests; the URL never goes on argv."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/healthcheck-ping.sh"


def run(tmp_path, *args, url="https://hc-ping.com/00000000-0000-0000-0000-000000000000", healthz="http://127.0.0.1:9/healthz"):
    f = tmp_path / "hc.url"
    if url is not None:
        f.write_text(url + "\n")
    env = dict(os.environ, HC_PING_URL_FILE=str(f), HC_HEALTHZ_URL=healthz)
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def test_unconfigured_sends_nothing_and_is_not_a_failure(tmp_path):
    r = run(tmp_path, url=None)
    assert r.returncode == 0 and "nothing sent" in r.stderr


def test_rejects_anything_but_hc_ping(tmp_path):
    r = run(tmp_path, "--dry-run", url="https://evil.example/x")
    assert r.returncode == 1 and "nothing sent" in r.stderr


def test_unreachable_healthz_goes_to_the_fail_endpoint(tmp_path):
    r = run(tmp_path, "--dry-run")
    assert r.returncode == 0 and "healthz=unreachable" in r.stdout and "fail endpoint" in r.stdout


def test_url_never_on_argv():
    src = SCRIPT.read_text()
    assert "-K -" in src and 'curl -fsS --max-time 15 --retry 3 --retry-delay 5 \\' in src
    assert '"${target}"' not in src.split("curl -fsS", 1)[1].split("\n")[0]
