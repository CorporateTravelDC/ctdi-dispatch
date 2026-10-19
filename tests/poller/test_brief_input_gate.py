"""2026-10-19 (lean-out L2, F7): ops-brief and ep-advance skip the model narrative when
the stable DATA fields of their input are unchanged since the last successful model run.
Operator rule: dedup never hashes timestamps -- a change in a pull stamp, a METAR issue
group, a cache age or a narrative text alone must NOT re-run the model; a data change must."""
from __future__ import annotations

import os
import time
import types

import pytest

os.environ.setdefault("DISPATCH_DB_BACKEND", "sqlite")

from common import input_gate as ig  # noqa: E402
from poller.skills import ep_advance_brief as ep  # noqa: E402
from poller.skills import ops_brief as ops  # noqa: E402


# ── gate unit ────────────────────────────────────────────────────────────────

def test_gate_reuses_only_same_key_within_cap(tmp_path):
    clock = [1000.0]
    g = ig.InputGate("t", state_dir=str(tmp_path), max_reuse_s=600, now=lambda: clock[0])
    assert g.reusable("k1") is None
    g.record("k1", "narr", "short")
    assert g.reusable("k1")["narrative"] == "narr"
    assert g.reusable("k2") is None
    clock[0] += 601
    assert g.reusable("k1") is None          # never older than the cap


def test_mask_stamps_keeps_event_fields():
    a = "METAR KDCA 190152Z 27010KT 10SM CLR\nAcela +5min (local-db, 3min old)\nFAA NAS as of Oct 19 01:50"
    b = "METAR KDCA 190252Z 27010KT 10SM CLR\nAcela +5min (local-db, 17min old)\nFAA NAS as of Oct 19 02:50"
    assert ig.mask_stamps(a) == ig.mask_stamps(b)
    c = "METAR KDCA 190252Z 28015G25KT 10SM CLR"
    assert ig.mask_stamps(a.splitlines()[0]) != ig.mask_stamps(c)
    assert ig.mask_stamps("GDP KDCA until 2100Z") != ig.mask_stamps("GDP KDCA until 2300Z")


def test_note_is_utc_and_et():
    st = {"model_at": 1760839200.0}          # 2025-10-19 02:00Z = 22:00 ET
    assert "02:00Z (22:00 ET)" in ig.InputGate.no_change_note(st)


# ── ops-brief ────────────────────────────────────────────────────────────────

@pytest.fixture
def ops_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(time, "localtime", lambda *a: types.SimpleNamespace(tm_hour=1))
    data = {"metar": "METAR KDCA 190152Z 27010KT 10SM CLR", "cps": {"score": 20, "label": "LOW", "narrative": "n1"},
            "amtrak": "Train 1 +5min (local-db, 3min old)"}
    calls = []
    sent = []
    monkeypatch.setattr(ops, "_fetch_atcscc_opsplan_sections", lambda: None)
    monkeypatch.setattr(ops, "_metar_section", lambda: data["metar"])
    monkeypatch.setattr(ops, "_nas_section", lambda: "FAA NAS as of " + data["metar"][11:17])
    monkeypatch.setattr(ops, "_atcscc_forecast_section", lambda pre=None: "forecast")
    monkeypatch.setattr(ops, "_nws_alerts_section", lambda: "none")
    monkeypatch.setattr(ops, "_amtrak_section", lambda: data["amtrak"])
    monkeypatch.setattr(ops, "_tfr_section", lambda: "No VIP TFRs.")
    monkeypatch.setattr(ops, "_route_section", lambda: "route " + data["cps"]["narrative"])
    monkeypatch.setattr(ops, "_geometry_section", lambda: "")
    monkeypatch.setattr(ops, "get_aam_watch_summary", lambda k: "")
    monkeypatch.setattr(ops, "get_disruption_weather_capsule", lambda: "")
    monkeypatch.setattr(ops, "log_usage", lambda *a, **k: None)
    monkeypatch.setattr(ops, "_send_ntfy_dual", lambda full, concise, title: sent.append(full))
    fake_db = types.SimpleNamespace(get_latest_cps=lambda: data["cps"], archive_brief=lambda *a, **k: None)
    monkeypatch.setattr(ops, "db", fake_db)

    def model(content):
        calls.append(content)
        return f"OPS BRIEF x (Ollama/m)\n\nnarrative {len(calls)}", "concise"
    monkeypatch.setattr(ops, "_call_ollama", model)
    return data, calls, sent


def test_ops_timestamp_only_change_skips_model(ops_env):
    data, calls, sent = ops_env
    ops.main()
    assert len(calls) == 1
    data["metar"] = "METAR KDCA 190252Z 27010KT 10SM CLR"           # new issue stamp only
    data["amtrak"] = "Train 1 +5min (local-db, 19min old)"           # age only
    data["cps"] = {"score": 20, "label": "LOW", "narrative": "n2"}   # narrative text only
    ops.main()
    assert len(calls) == 1
    assert "no change in input data since" in sent[-1] and "narrative 1" in sent[-1]


def test_ops_data_change_calls_model(ops_env):
    data, calls, sent = ops_env
    ops.main()
    data["metar"] = "METAR KDCA 190252Z 30020G30KT 2SM TSRA"
    ops.main()
    assert len(calls) == 2 and "narrative 2" in sent[-1]


def test_ops_force_and_fallback_never_recorded(ops_env, monkeypatch):
    data, calls, sent = ops_env
    ops.main()
    ops.main(force=True)
    assert len(calls) == 2
    # a failed model run is not recorded: next run with new data then same data still calls the model
    data["metar"] = "METAR KDCA 190252Z 30020KT 10SM CLR"
    monkeypatch.setattr(ops, "_call_ollama", lambda c: calls.append(c) and None)
    ops.main()
    ops.main()
    assert len(calls) == 4


# ── ep-advance ───────────────────────────────────────────────────────────────

@pytest.fixture
def ep_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(time, "localtime", lambda *a: types.SimpleNamespace(tm_hour=1))
    data = {"tfr": "No active TFRs.", "osint": "=== ACTIVE OSINT FEED (2 EP-relevant items last 24h) ===\n  [MED] a",
            "cps": {"score": 10, "label": "LOW", "narrative": "x"}}
    calls, sent = [], []
    monkeypatch.setattr(ep, "_tfr_section", lambda: data["tfr"])
    monkeypatch.setattr(ep, "_weather_section", lambda: "KDCA: 3000ft / 10SM / 8kt")
    monkeypatch.setattr(ep, "_nws_section", lambda: "none")
    monkeypatch.setattr(ep, "_route_section", lambda: "route")
    monkeypatch.setattr(ep, "_threat_sites_section", lambda: "threats")
    monkeypatch.setattr(ep, "_osint_section", lambda: data["osint"])
    monkeypatch.setattr(ep, "_cached_venue_section", lambda: "venues")
    monkeypatch.setattr(ep, "get_aam_watch_section", lambda k: "")
    monkeypatch.setattr(ep, "log_usage", lambda *a, **k: None)
    monkeypatch.setattr(ep, "db", types.SimpleNamespace(get_latest_cps=lambda: data["cps"],
                                                       archive_brief=lambda *a, **k: None))
    monkeypatch.setattr(ep._ntfy, "send", lambda topic, text, **k: sent.append((topic, text)))

    def model(prompt):
        calls.append(prompt)
        return f"EP-ADVANCE x (Ollama/m)\n\nep narrative {len(calls)}", "c"
    monkeypatch.setattr(ep, "_call_ollama", model)
    return data, calls, sent


def test_ep_count_or_narrative_only_change_skips(ep_env):
    data, calls, sent = ep_env
    ep.main()
    data["osint"] = data["osint"].replace("(2 EP", "(3 EP")
    data["cps"] = {"score": 10, "label": "LOW", "narrative": "changed words"}
    ep.main()
    assert len(calls) == 1
    full = [t for topic, t in sent if topic == "ep-advance"][-1]
    assert "no change in input data since" in full and "ep narrative 1" in full


def test_ep_data_change_calls_model(ep_env):
    data, calls, sent = ep_env
    ep.main()
    data["tfr"] = "VIP TFRs ACTIVE: 6/0100."
    ep.main()
    assert len(calls) == 2
