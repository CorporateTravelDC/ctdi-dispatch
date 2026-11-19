"""apt-snapshot-guard (2026-11-18): the Debian snapshot pin's expiry is watched, the next pin is
prepared (patch + relay) before it lapses, and a rollout refuses to build on an expired pin.
All network, gpgv, ntfy and time are stubbed through the script's APT_GUARD_* seams."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts/build/apt-snapshot-guard.py"
TEMPLATE = ROOT / "scripts/build/aptsnap-relay.template"
PIN = "20261118T160000Z"
NOW = int(datetime(2026, 11, 24, 0, 30, tzinfo=timezone.utc).timestamp())   # 22 h before expiry
SEC_EXPIRY = int(datetime(2026, 11, 24, 22, 33, tzinfo=timezone.utc).timestamp())
UPD_EXPIRY = int(datetime(2026, 11, 25, 14, 23, tzinfo=timezone.utc).timestamp())


def _inrelease(date_epoch, valid_until=None):
    d = format_datetime(datetime.fromtimestamp(date_epoch, timezone.utc), usegmt=True).replace("GMT", "UTC")
    lines = ["-----BEGIN PGP SIGNED MESSAGE-----", "Hash: SHA256", "", "Origin: Debian", f"Date: {d}"]
    if valid_until is not None:
        v = format_datetime(datetime.fromtimestamp(valid_until, timezone.utc), usegmt=True).replace("GMT", "UTC")
        lines.append(f"Valid-Until: {v}")
    return "\n".join(lines + ["-----BEGIN PGP SIGNATURE-----", "x", "-----END PGP SIGNATURE-----", ""])


def _fixture(fx: Path, snap: str, sec_until, upd_until, base_date=1789000000):
    d = fx / snap
    d.mkdir(parents=True, exist_ok=True)
    (d / "trixie.InRelease").write_text(_inrelease(base_date))
    (d / "trixie-updates.InRelease").write_text(_inrelease(upd_until - 7 * 86400, upd_until))
    (d / "trixie-security.InRelease").write_text(_inrelease(sec_until - 7 * 86400, sec_until))


def _repo(tmp_path: Path, pin=PIN) -> Path:
    """A tiny git repo with two Containerfiles and the real pin tool."""
    repo = tmp_path / "repo"
    (repo / "scripts/build").mkdir(parents=True)
    for name in ("pin-base-images.py", "buildlib.py"):
        src = ROOT / "scripts/build" / name
        (repo / "scripts/build" / name).write_text(src.read_text())
    (repo / "build").mkdir()
    (repo / "build/policy.toml").write_text((ROOT / "build/policy.toml").read_text())
    for cf in ("Containerfile.ingest", "Containerfile.web"):
        (repo / cf).write_text(f"FROM docker.io/library/python:3.13-slim@sha256:{'a' * 64}\n"
                               f"ARG APT_SNAPSHOT={pin}\nRUN true\n")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t.invalid")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "base", "--no-verify"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env, capture_output=True)
    return repo


def _run(tmp_path, *args, now=NOW, gpgv="skip", fixtures=None, repo=None):
    kr = tmp_path / "keyring.gpg"
    kr.write_bytes(b"stub")
    st = tmp_path / "state"
    drop = tmp_path / "drop"
    env = dict(os.environ,
               APT_GUARD_NOW=str(now), APT_GUARD_FIXTURES=str(fixtures or tmp_path / "fx"),
               APT_GUARD_GPGV=gpgv, APT_GUARD_NTFY_LOG=str(tmp_path / "ntfy.log"),
               APT_GUARD_REPO=str(repo or _repo(tmp_path)), APT_GUARD_STATE_DIR=str(st),
               APT_GUARD_DROP_DIR=str(drop), APT_GUARD_PODMAN="skip", USER="corporatetraveldc",
               APT_GUARD_KEYRING=str(kr))
    r = subprocess.run([sys.executable, str(GUARD), *args], env=env, capture_output=True, text=True, timeout=120)
    return r, st, drop


def _alerts(tmp_path):
    p = tmp_path / "ntfy.log"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _stub_gpgv(tmp_path, rc):
    p = tmp_path / f"gpgv-{rc}"
    p.write_text(f"#!/bin/sh\nexit {rc}\n")
    p.chmod(0o755)
    return str(p)


def test_expiry_is_the_earliest_valid_until(tmp_path):
    _fixture(tmp_path / "fx", PIN, SEC_EXPIRY, UPD_EXPIRY)
    r, st, _ = _run(tmp_path, now=SEC_EXPIRY - 10 * 86400)
    assert r.returncode == 0, r.stderr
    s = json.loads((st / "state.json").read_text())
    assert s["pin"] == PIN and s["expiry"] == "2026-11-24T22:33Z" and s["verified"] is True
    assert s["suites"]["trixie"]["valid_until"] is None          # the base suite carries none
    assert _alerts(tmp_path) == []                                 # 10 days left: quiet


def test_48h_warns_once(tmp_path):
    _fixture(tmp_path / "fx", PIN, SEC_EXPIRY, UPD_EXPIRY)
    repo = _repo(tmp_path)
    for _ in range(2):
        r, _, drop = _run(tmp_path, now=SEC_EXPIRY - 40 * 3600, repo=repo)
        assert r.returncode == 0, r.stderr
    a = _alerts(tmp_path)
    assert [x["priority"] for x in a] == [3]
    assert not drop.exists()                                       # no prefetch before 24 h


def test_24h_prefetches_a_valid_candidate_and_is_idempotent(tmp_path):
    fx = tmp_path / "fx"
    _fixture(fx, PIN, SEC_EXPIRY, UPD_EXPIRY)
    cand = "20261124T000000Z"
    _fixture(fx, cand, NOW + 7 * 86400, NOW + 7 * 86400)
    repo = _repo(tmp_path)
    r, st, drop = _run(tmp_path, repo=repo, gpgv=_stub_gpgv(tmp_path, 0))
    assert r.returncode == 0, r.stdout + r.stderr
    out = drop / f"aptsnap-{cand}-from-corporatetraveldc"
    patch = out / f"aptsnap-{cand}.patch"
    relay = (out / "aptsnap.relay").read_text()
    body = patch.read_text()
    assert f"+ARG APT_SNAPSHOT={cand}" in body and f"-ARG APT_SNAPSHOT={PIN}" in body
    assert "@@" not in relay and "COUNT=2" in relay and f"NEW={cand}" in relay
    assert "signature: verified" in relay
    import hashlib
    assert f"PATCH_SHA={hashlib.sha256(patch.read_bytes()).hexdigest()}" in relay
    assert (repo / "Containerfile.ingest").read_text().count(PIN) == 1   # the checkout is untouched
    a = _alerts(tmp_path)
    assert [x["priority"] for x in a] == [4] and "aptsnap.relay" in a[0]["msg"]
    # second run: same pin -> nothing re-prepared, no repeat alert
    mtime = patch.stat().st_mtime_ns
    r2, _, _ = _run(tmp_path, repo=repo, gpgv=_stub_gpgv(tmp_path, 0), now=NOW + 3600)
    assert r2.returncode == 0 and patch.stat().st_mtime_ns == mtime
    assert len(_alerts(tmp_path)) == 1
    assert "already prepared" in r2.stdout


def test_generated_relay_passes_relay_lint(tmp_path):
    fx = tmp_path / "fx"
    _fixture(fx, PIN, SEC_EXPIRY, UPD_EXPIRY)
    _fixture(fx, "20261124T000000Z", NOW + 7 * 86400, NOW + 7 * 86400)
    r, _, drop = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    relay = next(drop.glob("aptsnap-*/aptsnap.relay"))
    # the drop path of a test run sits under pytest's tmp dir, whose name relay-lint would read as a
    # pytest step; lint the relay with the drop path it has in production
    text = "\n".join("PATCH=/srv/ctdc-handoff/aptsnap-x-from-corporatetraveldc/aptsnap-x.patch"
                     if ln.startswith("PATCH=") else ln for ln in relay.read_text().splitlines()) + "\n"
    linted = tmp_path / "aptsnap.relay"
    linted.write_text(text)
    lint = subprocess.run([sys.executable, str(ROOT / "scripts/relay-lint.py"), str(linted)],
                          capture_output=True, text=True, timeout=60)
    assert lint.returncode == 0, lint.stdout + lint.stderr


@pytest.mark.parametrize("hours_left,prio", [(5, 5), (-1, 5)])
def test_6h_and_expired_are_urgent(tmp_path, hours_left, prio):
    fx = tmp_path / "fx"
    _fixture(fx, PIN, SEC_EXPIRY, UPD_EXPIRY)
    now = SEC_EXPIRY - hours_left * 3600
    from datetime import datetime as _d
    cand = _d.fromtimestamp(now, timezone.utc).replace(minute=0, second=0).strftime("%Y%m%dT%H%M%SZ")
    _fixture(fx, cand, now + 7 * 86400, now + 7 * 86400)
    r, _, _ = _run(tmp_path, now=now)
    assert r.returncode == 0, r.stderr
    assert max(x["priority"] for x in _alerts(tmp_path)) == prio


@pytest.mark.parametrize("problem", ["short", "badsig", "missing", "notlater"])
def test_bad_candidate_drops_nothing_and_alerts(tmp_path, problem):
    fx = tmp_path / "fx"
    _fixture(fx, PIN, SEC_EXPIRY, UPD_EXPIRY)
    cand = "20261124T000000Z"
    gpgv = "skip"
    if problem == "short":
        _fixture(fx, cand, NOW + 2 * 86400, NOW + 2 * 86400)
    elif problem == "notlater":     # Debian has not published newer Release files yet
        _fixture(fx, cand, SEC_EXPIRY, UPD_EXPIRY)
        os.environ["APT_GUARD_MIN_VALID_DAYS"] = "0"
    elif problem == "badsig":
        _fixture(fx, cand, NOW + 7 * 86400, NOW + 7 * 86400)
        gpgv = _stub_gpgv(tmp_path, 1)
    try:
        r, _, drop = _run(tmp_path, gpgv=gpgv)
    finally:
        os.environ.pop("APT_GUARD_MIN_VALID_DAYS", None)
    assert r.returncode == 0, r.stderr
    assert not drop.exists() or not any(drop.iterdir())
    titles = [x["title"] for x in _alerts(tmp_path)]
    assert "Debian snapshot: next pin NOT prepared" in titles


def test_rollout_refuses_within_2h_and_names_the_relay(tmp_path):
    fx = tmp_path / "fx"
    _fixture(fx, PIN, SEC_EXPIRY, UPD_EXPIRY)
    _fixture(fx, "20261124T000000Z", NOW + 7 * 86400, NOW + 7 * 86400)
    repo = _repo(tmp_path)
    _run(tmp_path, repo=repo)                                      # prepares at 22 h
    r, _, _ = _run(tmp_path, "--check-rollout", repo=repo, now=SEC_EXPIRY - 3600)
    assert r.returncode == 2 and "REFUSING" in r.stdout and "aptsnap.relay" in r.stdout
    ok, _, _ = _run(tmp_path, "--check-rollout", repo=repo, now=SEC_EXPIRY - 3 * 86400)
    assert ok.returncode == 0 and "valid to 2026-11-24T22:33Z" in ok.stdout


def test_network_down_is_fail_soft_but_cached_expiry_still_counts(tmp_path):
    fx = tmp_path / "fx"
    _fixture(fx, PIN, SEC_EXPIRY, UPD_EXPIRY)
    repo = _repo(tmp_path)
    _run(tmp_path, repo=repo, now=SEC_EXPIRY - 5 * 86400)          # caches the expiry
    empty = tmp_path / "nofx"
    empty.mkdir()
    r, st, _ = _run(tmp_path, "--check-rollout", repo=repo, fixtures=empty, now=SEC_EXPIRY - 4 * 86400)
    assert r.returncode == 0
    assert json.loads((st / "state.json").read_text())["verified"] is False
    r2, _, _ = _run(tmp_path, "--check-rollout", repo=repo, fixtures=empty, now=SEC_EXPIRY + 60)
    assert r2.returncode == 2                                     # known expired: time alone decides
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r3, _, _ = _run(fresh, "--check-rollout", fixtures=empty, now=NOW)  # nothing cached: warn only
    assert r3.returncode == 0 and "WARNING" in r3.stdout


def test_serialized_rollout_calls_the_guard_before_building():
    s = (ROOT / "scripts/serialized-rollout.sh").read_text()
    i = s.index("apt-snapshot-guard.py\" --check-rollout")
    assert i < s.index("podman build") if "podman build" in s else i > 0
    assert "exit 4" in s[i:i + 200]
    assert "ALLOW_EXPIRED_SNAPSHOT" in s and 'FROM_REFRESH}" != 1' in s


def test_units_and_template_shape():
    timer = (ROOT / ".config/systemd/user/corporatetraveldc-apt-snapshot-guard.timer").read_text()
    svc = (ROOT / ".config/systemd/user/corporatetraveldc-apt-snapshot-guard.service").read_text()
    assert "Persistent=true" in timer and "RandomizedDelaySec=" in timer and "00/6" in timer
    assert "Requires=" not in timer.split("[Timer]")[0].replace("Requires=/Wants=", "")
    assert "apt-snapshot-guard.py" in svc and "Type=oneshot" in svc
    t = TEMPLATE.read_text()
    for token in ("@@OLD@@", "@@NEW@@", "@@PATCH@@", "@@SHA@@", "@@BASE@@", "@@COUNT@@"):
        assert token in t
    assert "RELAY_DONE=1" in t and "set -euo pipefail" in t
