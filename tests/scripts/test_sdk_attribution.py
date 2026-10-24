"""2026-10-24: scripts/sdk-attribution.py -- SDK/telemetry host -> app
attribution by DNS co-occurrence, on a synthetic Pi-hole v6 FTL database
(query_storage / domain_by_id / client_by_id). No real data, no network: the
alert hook is replaced by a recorder, the client IP is a TEST-NET address.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEST_IP = "198.51.100.7"          # TEST-NET-2, never a real client
IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
BLOCKED, ALLOWED = 1, 2
BIN = 120


def load_module():
    spec = importlib.util.spec_from_file_location("sdk_attribution", REPO / "scripts/sdk-attribution.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FakeFTL:
    """Builds the v6 schema the script queries. add() takes a bin index
    relative to `base` so tests can place queries in shared or disjoint
    2-minute bins without thinking about epoch seconds."""

    def __init__(self, path: Path):
        self.path = path
        self.base = (int(time.time()) - 8 * 3600) // BIN * BIN
        self.con = sqlite3.connect(path)
        self.con.executescript(
            "CREATE TABLE client_by_id (id INTEGER PRIMARY KEY, ip TEXT NOT NULL, name TEXT);"
            "CREATE TABLE domain_by_id (id INTEGER PRIMARY KEY, domain TEXT NOT NULL UNIQUE);"
            "CREATE TABLE query_storage (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,"
            " type INTEGER NOT NULL, status INTEGER NOT NULL, domain INTEGER NOT NULL, client INTEGER NOT NULL,"
            " forward INTEGER, additional_info INTEGER, reply_type INTEGER, reply_time REAL, dnssec INTEGER,"
            " list_id INTEGER, ede INTEGER);"
        )
        self.con.execute("INSERT INTO client_by_id (id, ip) VALUES (1, ?)", (TEST_IP,))
        self.con.execute("INSERT INTO client_by_id (id, ip) VALUES (2, ?)", ("198.51.100.99",))
        self.con.commit()

    def add(self, bin_idx: int, domain: str, status: int, client: int = 1, offset: int = 5):
        self.con.execute("INSERT OR IGNORE INTO domain_by_id (domain) VALUES (?)", (domain,))
        did = self.con.execute("SELECT id FROM domain_by_id WHERE domain = ?", (domain,)).fetchone()[0]
        self.con.execute(
            "INSERT INTO query_storage (timestamp, type, status, domain, client) VALUES (?, 1, ?, ?, ?)",
            (self.base + bin_idx * BIN + offset, status, did, client),
        )

    def commit(self):
        self.con.commit()


def build_scenario(db: FakeFTL, *, mcd_bins=range(0, 40), nwfcu_bins=range(60, 100), extra=None):
    """McDonald's app bins carry sdk.split.io (blocked) + api.mcd.com (allowed);
    credit-union bins carry device-api.urbanairship.com (blocked) +
    app.nwfcu.org (allowed); an allowed braze host rides with McDonald's;
    google/cdn infrastructure is in every bin; 40 idle bins have only infra."""
    for b in mcd_bins:
        db.add(b, "sdk.split.io", BLOCKED)
        db.add(b, "api.mcd.com", ALLOWED)
        db.add(b, "sdk.fra-01.braze.eu", ALLOWED)
    for b in nwfcu_bins:
        db.add(b, "device-api.urbanairship.com", BLOCKED)
        db.add(b, "app.nwfcu.org", ALLOWED)
    for b in range(0, 140):
        db.add(b, "www.google.com", ALLOWED)
        db.add(b, "static.somecdn.net", ALLOWED)
    # a chatty allowed host that is NOT an SDK: cache-stale answers, never blocked
    for b in range(0, 30):
        db.add(b, "sync.mailprovider.example", 17)
    # another client's traffic must be ignored entirely
    for b in range(0, 40):
        db.add(b, "sdk.other-client.io", BLOCKED, client=2)
    if extra:
        extra(db)
    db.commit()


@pytest.fixture
def env(tmp_path, monkeypatch):
    m = load_module()
    sent = []
    monkeypatch.setattr(m, "send_alert", lambda title, msg: sent.append((title, msg)))
    monkeypatch.setenv("SDK_ATTRIBUTION_CLIENT_IPS", TEST_IP)
    db = FakeFTL(tmp_path / "ftl.db")
    report_dir = tmp_path / "reports"
    state = tmp_path / "state.json"

    def run(*extra_args):
        argv = ["--ftl-db", str(db.path), "--report-dir", str(report_dir), "--state", str(state),
                "--env-file", str(tmp_path / "absent.env"), *extra_args]
        assert m.main(argv) == 0

    return m, db, run, sent, report_dir, state


def _report(report_dir: Path) -> dict:
    js = sorted(report_dir.glob("*.json"))
    assert js, "no JSON report written"
    return json.loads(js[-1].read_text())


def _host(report: dict, name: str) -> dict:
    return next(h for h in report["hosts"] if h["host"] == name)


# ----------------------------------------------------------------- attribution
def test_attribution_picks_the_co_occurring_app(env):
    m, db, run, sent, report_dir, state = env
    build_scenario(db)
    run()
    rep = _report(report_dir)
    assert _host(rep, "sdk.split.io")["top"][0]["anchor"] == "mcd.com"
    assert _host(rep, "device-api.urbanairship.com")["top"][0]["anchor"] == "nwfcu.org"
    split = _host(rep, "sdk.split.io")
    assert split["blocked"] == 40 and split["allowed"] == 0 and split["top"][0]["n"] == 40
    # the allowed braze host is an SDK host by pattern and attributes to McDonald's too
    braze = _host(rep, "sdk.fra-01.braze.eu")
    assert braze["blocked"] == 0 and braze["known_sdk_pattern"] and braze["top"][0]["anchor"] == "mcd.com"


def test_infra_and_sdk_hosts_never_become_anchors(env):
    m, db, run, sent, report_dir, state = env
    build_scenario(db)
    run()
    anchors = {t["anchor"] for h in _report(report_dir)["hosts"] for t in h["top"]}
    assert "google.com" not in anchors and "somecdn.net" not in anchors
    assert "braze.eu" not in anchors and "split.io" not in anchors
    assert m.registrable("app.example.co.uk") == "example.co.uk"
    assert m.registrable("api.mcd.com") == "mcd.com"


def test_blocked_status_set_and_by_count_rule(env):
    m, db, run, sent, report_dir, state = env
    assert m.BLOCKED_STATUSES == {1, 4, 5, 6, 7, 8, 9, 10, 11, 15, 16}
    for s in (2, 3, 12, 13, 14, 17):
        assert s not in m.BLOCKED_STATUSES

    def extra(db):
        for b in range(0, 25):
            db.add(b, "tracker.unlisted.example", 9)     # gravity-CNAME block: counts
        for b in range(0, 25):
            db.add(b, "quiet.unlisted.example", 5)       # 25 denylist blocks: counts
        for b in range(0, 19):
            db.add(b, "under.unlisted.example", 1)       # 19 < 20: not an SDK host
    build_scenario(db, extra=extra)
    run()
    hosts = {h["host"]: h for h in _report(report_dir)["hosts"]}
    assert "tracker.unlisted.example" in hosts and not hosts["tracker.unlisted.example"]["known_sdk_pattern"]
    assert "quiet.unlisted.example" in hosts
    assert "under.unlisted.example" not in hosts
    assert "sync.mailprovider.example" not in hosts     # 30 allowed (status 17) lookups, zero blocked
    assert "sdk.other-client.io" not in hosts            # other client


# ---------------------------------------------------------------------- alerts
def test_seed_run_is_silent_then_new_host_alerts_once(env):
    m, db, run, sent, report_dir, state = env
    build_scenario(db)
    run()
    assert sent == [] and json.loads(state.read_text())["seeded"]
    run()
    assert sent == [], "a steady state must not alert"

    for b in range(0, 40):            # rides the McDonald's bins, like split.io
        db.add(b, "identity.mparticle.com", BLOCKED)
    db.commit()
    run()
    assert len(sent) == 1
    title, msg = sent[0]
    assert "new SDK host" in title and "identity.mparticle.com" in msg and "-> mcd.com" in msg
    run()
    assert len(sent) == 1, "a new host alerts exactly once"


def test_top_app_change_alert_respects_threshold(env):
    m, db, run, sent, report_dir, state = env
    build_scenario(db)
    run()                                       # seed: split.io -> mcd.com (n=40)
    assert json.loads(state.read_text())["hosts"]["sdk.split.io"]["top"] == "mcd.com"

    # Change with weak evidence (n=10 shared bins): recorded, no alert.
    db2 = FakeFTL(db.path.with_name("ftl2.db"))
    for b in range(0, 10):
        db2.add(b, "sdk.split.io", BLOCKED)
        db2.add(b, "order.burgerplace.example", ALLOWED)
    for b in range(0, 140):
        db2.add(b, "www.google.com", ALLOWED)
    db2.commit()
    m.main(["--ftl-db", str(db2.path), "--report-dir", str(report_dir), "--state", str(state),
            "--env-file", "/nonexistent"])
    assert sent == []
    st = json.loads(state.read_text())["hosts"]["sdk.split.io"]
    assert st["top"] == "mcd.com" and st["latest_top"] == "burgerplace.example"

    # Same change with n=20 >= 15: one alert naming old -> new.
    for b in range(10, 20):
        db2.add(b, "sdk.split.io", BLOCKED)
        db2.add(b, "order.burgerplace.example", ALLOWED)
    db2.commit()
    m.main(["--ftl-db", str(db2.path), "--report-dir", str(report_dir), "--state", str(state),
            "--env-file", "/nonexistent"])
    assert len(sent) == 1 and "mcd.com -> burgerplace.example (n=20)" in sent[0][1]
    assert json.loads(state.read_text())["hosts"]["sdk.split.io"]["top"] == "burgerplace.example"


def test_backfill_seeds_without_alerting_and_dry_run_writes_nothing(env):
    m, db, run, sent, report_dir, state = env
    build_scenario(db)
    run("--dry-run")
    assert not report_dir.exists() and not state.exists()
    run("--backfill-days", "3")
    assert sent == []
    assert any(p.name.endswith("-backfill.json") for p in report_dir.iterdir())
    assert any(p.name.endswith("-backfill.txt") for p in report_dir.iterdir())
    run("--backfill-days", "3")   # a second backfill with state present still never alerts
    assert sent == []


# ---------------------------------------------------------------------- config
def test_config_is_parsed_never_sourced(tmp_path, monkeypatch):
    m = load_module()
    monkeypatch.delenv("SDK_ATTRIBUTION_CLIENT_IPS", raising=False)
    monkeypatch.delenv("UBER_WATCH_CLIENT_IP", raising=False)
    marker = tmp_path / "sourced.marker"
    envf = tmp_path / "dispatch.env"
    envf.write_text(
        "# comment\n"
        f"BOOM=$(touch {marker})\n"
        "UBER_WATCH_CLIENT_IP=198.51.100.7\n"
        'export SDK_ATTRIBUTION_CLIENT_IPS="198.51.100.7, 198.51.100.8"\n'
        "NTFY_TOKEN=not-a-real-token\n"
    )
    assert m.client_ips(str(envf)) == ["198.51.100.7", "198.51.100.8"]
    assert not marker.exists(), "the env file must be parsed, never sourced"
    envf.write_text("UBER_WATCH_CLIENT_IP=198.51.100.7\n")
    assert m.client_ips(str(envf)) == ["198.51.100.7"]          # falls back to the dns-anomaly key
    assert m.client_ips(str(tmp_path / "absent.env")) == [m.DEFAULT_CLIENT_IP]
    monkeypatch.setenv("SDK_ATTRIBUTION_CLIENT_IPS", "198.51.100.9")
    assert m.client_ips(str(envf)) == ["198.51.100.9"]           # environment wins (tests)


# --------------------------------------------------------------- output hygiene
def test_report_files_written_and_no_ip_in_any_output(env, capsys):
    m, db, run, sent, report_dir, state = env
    build_scenario(db)
    run()
    for b in range(0, 30):
        db.add(b, "identity.mparticle.com", BLOCKED)
    db.commit()
    run()
    out = capsys.readouterr().out
    txts = list(report_dir.glob("*.txt"))
    assert txts and list(report_dir.glob("*.json"))
    assert txts[0].name == f"{txts[0].stem}.txt" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", txts[0].stem)
    body = txts[0].read_text()
    assert "sdk host" in body and "sdk.split.io" in body and "mcd.com" in body
    for blob in (out, body, *(p.read_text() for p in report_dir.glob("*.json")), state.read_text(),
                 *(t + " " + msg for t, msg in sent)):
        assert not IP_RE.search(blob), "client IPs must never appear in output"
    assert TEST_IP not in out


def test_unit_pair_is_tracked_and_sane():
    svc = (REPO / ".config/systemd/user/corporatetraveldc-sdk-attribution.service").read_text()
    tmr = (REPO / ".config/systemd/user/corporatetraveldc-sdk-attribution.timer").read_text()
    assert "Type=oneshot" in svc and "Nice=19" in svc and "IOSchedulingClass=idle" in svc
    assert "sdk-attribution.py --days 1" in svc
    assert "OnCalendar=*-*-* 04:20:00 America/New_York" in tmr
    assert "RandomizedDelaySec=300" in tmr and "Persistent=false" in tmr
    assert "Requires=" not in tmr.split("[Timer]")[1] and "Wants=" not in tmr.split("[Timer]")[1]
    assert not re.search(r"^(Requires|Wants)=", tmr, re.M)
