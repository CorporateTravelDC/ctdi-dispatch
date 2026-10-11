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
