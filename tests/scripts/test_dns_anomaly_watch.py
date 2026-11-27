"""2026-10-10: DNS anomaly watch follows the whole CNAME chain (the tb-static
false positive) and renders one readable line per domain."""
from __future__ import annotations

import importlib.util
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load(monkeypatch, dig_out: str):
    spec = importlib.util.spec_from_file_location("daw", REPO / "scripts/dns-anomaly-watch.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=dig_out, returncode=0, stderr=""))
    return m


def test_migration_chain_ending_in_the_vendor_family_is_known_good(monkeypatch):
    m = load(monkeypatch, "tb-static-migration.uber.com.\nfrontends-cloud.uber.com.\ncn-neg.cfe.uber.com.\n104.36.194.9\n")
    assert m.classify_uber_domain("tb-static.uber.com") == "known-good"


def test_a_chain_outside_the_family_is_anomalous_and_readable(monkeypatch):
    m = load(monkeypatch, "edge.example-cdn.net.\n203.0.113.7\n")
    v = m.classify_uber_domain("new.uber.com")
    assert v == "ANOMALOUS: new.uber.com -> edge.example-cdn.net (203.0.113.7)"


def test_state_paths_renamed_with_migration_source():
    src = (REPO / "scripts/dns-anomaly-watch.py").read_text()
    assert 'STATE_FILE = "/var/lib/corporatetraveldc/dns_anomaly_watch_state.json"' in src
    assert "OLD_STATE_FILE" in src and 'LOG_PREFIX = "dns-anomaly-watch:"' in src


def test_known_blocked_uber_domains_are_all_tracked(monkeypatch):
    # 2026-11-12 parity: every blocked *.uber.com name feeds blocked-hit tracking.
    m = load(monkeypatch, "")
    uber_tracked = {d for d in m.TRACKED_ANOMALY_ENDPOINTS if d.endswith(".uber.com")}
    assert m.KNOWN_BLOCKED == uber_tracked
    assert {"cn-geo1.uber.com", "pusher.uber.com", "vehicles.uber.com",
            "driver.android.mobile-config.uber.com"} <= m.KNOWN_BLOCKED


def test_unmirrored_uber_denylist_entry_is_logged_not_alerted(monkeypatch, capsys):
    m = load(monkeypatch, "")
    rows = sorted(m.TRACKED_ANOMALY_ENDPOINTS) + ["new-block.uber.com", "ads.example.com"]
    monkeypatch.setattr(m, "query_pihole", lambda sql, db=None: rows)
    assert m.check_denylist_gaps() == []
    out = capsys.readouterr().out
    assert "['new-block.uber.com']" in out and "ads.example.com" not in out


# ---- 2026-11-27: DNS BYPASS class from the dns-leak-watch collector (latest.json) -----------------------------
import json as _json

NOW = 1_795_000_000.0
CLIENT = "192.0.2.10"


def _doc(findings, generated=NOW, clients=("192.0.2.10/32",), red=0, hard=()):
    return {"schema": 1, "generated": generated, "clients": list(clients),
            "bypass_evidence": {"findings": list(findings), "dnslock_redirect_last_delta": red},
            "hardcoded": {"findings": list(hard)}}


def _f(cls, dest, port, first, proto="tcp"):
    return {"key": f"{cls}|{dest}|{proto}|{port}", "class": cls, "dest": dest, "proto": proto, "port": port,
            "first_seen": first, "conns": 1, "rdns": ""}


def _setup(monkeypatch, tmp_path, doc):
    m = load(monkeypatch, "")
    sent = []
    monkeypatch.setattr(m, "ntfy_alert", lambda cfg, msg, title, priority=4: sent.append((title, priority, msg)))
    p = tmp_path / "latest.json"; p.write_text(_json.dumps(doc))
    return m, sent, {"DNS_BYPASS_WATCH_JSON": str(p)}


def test_bypass_feature_off_touches_nothing(monkeypatch):
    m = load(monkeypatch, "")
    state = {"last_marker_ts": {}}
    assert m.bypass_pass({}, state, NOW, CLIENT, []) == [] and state == {"last_marker_ts": {}}


def test_bypass_first_pass_is_a_silent_baseline_then_alerts_once(monkeypatch, tmp_path):
    old = _f("dot", "203.0.113.1", 853, NOW - 600)
    m, sent, cfg = _setup(monkeypatch, tmp_path, _doc([old]))
    state = {"last_marker_ts": {}}
    assert m.bypass_pass(cfg, state, NOW, CLIENT, []) == [] and sent == []
    new = _f("doh", "203.0.113.53", 443, NOW - 30)
    (tmp_path / "latest.json").write_text(_json.dumps(_doc([new, old])))
    assert [f["key"] for f in m.bypass_pass(cfg, state, NOW, CLIENT, [])] == [new["key"]]
    assert len(sent) == 1 and sent[0][1] == 4 and "DNS over HTTPS" in sent[0][2] and "203.0.113.53" in sent[0][2]
    assert m.bypass_pass(cfg, state, NOW + 120, CLIENT, []) == [] and len(sent) == 1       # once only


def test_bypass_correlated_with_a_reconnect_is_priority_5(monkeypatch, tmp_path):
    m, sent, cfg = _setup(monkeypatch, tmp_path, _doc([]))
    state = {"last_marker_ts": {}, "bypass_alerted": []}
    (tmp_path / "latest.json").write_text(_json.dumps(_doc([_f("doq", "203.0.113.2", 853, NOW - 60, "udp")], red=3)))
    m.bypass_pass(cfg, state, NOW, CLIENT, [NOW - 120])
    title, prio, msg = sent[0]
    assert prio == 5 and "during a reconnect burst" in title and "DNS over QUIC" in msg and "3 hard-coded plain-DNS" in msg


def test_bypass_skips_stale_wrong_schema_and_other_clients(monkeypatch, tmp_path, capsys):
    f = _f("dns53", "203.0.113.3", 53, NOW - 10, "udp")
    for doc, why in [(_doc([f], generated=NOW - 3600), "older than"), ({**_doc([f]), "schema": 2}, "schema"),
                     (_doc([f], clients=("192.0.2.99/32",)), "does not watch")]:
        m, sent, cfg = _setup(monkeypatch, tmp_path, doc)
        state = {"last_marker_ts": {}, "bypass_alerted": []}
        assert m.bypass_pass(cfg, state, NOW, CLIENT, []) == [] and sent == []
        assert why in capsys.readouterr().out


def test_main_calls_bypass_only_when_configured():
    src = (REPO / "scripts/dns-anomaly-watch.py").read_text()
    assert 'if cfg.get("DNS_BYPASS_WATCH_JSON", "").strip():\n        bypass_pass(' in src
    assert "bypass_pass(cfg, state, now, client_ip, [])" in src           # also on a pass with no new queries


def test_bypass_host_filter_for_a_tailnet_wide_collector(monkeypatch, tmp_path):
    m, sent, cfg = _setup(monkeypatch, tmp_path, _doc([]))
    state = {"last_marker_ts": {}, "bypass_alerted": []}
    mine, other = _f("dot", "203.0.113.4", 853, NOW - 30), _f("dot", "203.0.113.5", 853, NOW - 30)
    mine["host"], other["host"] = "driver-phone", "laptop"
    (tmp_path / "latest.json").write_text(_json.dumps(_doc([mine, other])))
    got = m.bypass_pass({**cfg, "DNS_BYPASS_HOST": "driver-phone"}, state, NOW, CLIENT, [])
    assert [f["dest"] for f in got] == ["203.0.113.4"] and "203.0.113.5" not in sent[0][2]
    state2 = {"last_marker_ts": {}, "bypass_alerted": []}
    sent.clear()
    m.bypass_pass(cfg, state2, NOW, CLIENT, [])
    assert "from laptop" in sent[0][2] and "from driver-phone" in sent[0][2]
