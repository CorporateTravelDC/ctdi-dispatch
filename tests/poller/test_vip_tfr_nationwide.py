"""2026-10-13/14 (operator): VIP TFRs are nationwide. The FAA marks them type "VIP" with a plain
place/date description, so the text patterns never matched one (69 rows, 0 is_vip, 9 active VIP
TFRs). DC area keeps the hot path (p5 + Pushover + hourly heartbeat); elsewhere one p4
tfr-alert per new or changed TFR. CRANE is a flight identifier only."""
import json

import pusher.main as pm
from poller.fetchers import tfr
from ingest import local_airspace as la


def test_faa_type_vip_is_vip_without_any_text_match():
    assert tfr.is_vip("Anchorage, AK, Tuesday, October 13, 2026", "VIP")
    assert not tfr.is_vip("Anchorage, AK, hazards", "HAZARDS")
    assert tfr.is_vip("MOVEMENT OF VIP", "SECURITY")


def _row(tid, facility, desc, state="AK", enriched=None):
    return {"tfr_id": tid, "is_vip": 1, "enriched_text": enriched,
            "effective_start": 1792000800, "effective_end": 1792008000,
            "raw_json": json.dumps({"facility": facility, "state": state, "type": "VIP",
                                    "description": desc, "mod_abs_time": "1"})}


def _wire(monkeypatch, rows):
    sent, store = [], {}
    monkeypatch.setattr(pm.db, "get_active_tfrs", lambda: rows)
    monkeypatch.setattr(pm.db, "mark_tfr_notified", lambda t: None)
    monkeypatch.setattr(pm, "hot_push", lambda topic, m, title: sent.append((topic, m)) or True)
    monkeypatch.setattr(pm._tfr_dedup, "should_push", lambda k, h: store.get(k) != h)
    monkeypatch.setattr(pm._tfr_dedup, "record", lambda k, h: store.__setitem__(k, h))
    return sent


def test_push_is_the_vip_text_only(monkeypatch):
    rows = [_row("6/9195", "ZAN", "Anchorage, AK, Tuesday, October 13, 2026",
                 enriched="[02:18Z] TFRs: 77 active ... KDCA weather"),
            _row("6/1", "ZDC", "WASHINGTON, DC", state="DC", enriched="some narrative")]
    sent = _wire(monkeypatch, rows)
    pm.push_vip_tfrs()
    msgs = [m for topic, m in sent if topic == "tfr-alert"]
    win = "Active 18:00Z / 14:00 ET Wed 10-14 to 20:00Z / 16:00 ET Wed 10-14"
    assert msgs == [f"VIP TFR 6/9195: Anchorage, AK, Tuesday, October 13, 2026\n{win}", f"VIP TFR 6/1: WASHINGTON, DC\n{win}"]
    assert len([1 for topic, m in sent if topic == "hot-alerts"]) == 2       # original path: tfr-alert + hot-alerts


def test_a_new_narrative_or_stamp_never_refires(monkeypatch):           # the 10-14 rehash spam
    rows = [_row("6/1", "ZDC", "WASHINGTON, DC", state="DC", enriched="[02:18Z] x")]
    sent = _wire(monkeypatch, rows)
    pm.push_vip_tfrs()
    rows[0]["enriched_text"] = "[02:23Z] y"
    pm.push_vip_tfrs()
    assert len([1 for topic, m in sent if topic == "tfr-alert"]) == 1


def test_no_hourly_refire_while_active(monkeypatch):
    rows = [_row("6/1", "ZDC", "WASHINGTON, DC", state="DC")]
    sent = _wire(monkeypatch, rows)
    for _ in range(3):
        pm.push_vip_tfrs()
    assert len([1 for topic, m in sent if topic == "tfr-alert"]) == 1
    import inspect
    assert "_tfr_dedup.should_push_periodic" not in inspect.getsource(pm.push_vip_tfrs)


def test_cutover_silences_the_vip_tfrs_active_at_first_start(monkeypatch, tmp_path):
    rows = [_row("6/9195", "ZAN", "Anchorage, AK")]
    sent = _wire(monkeypatch, rows)
    monkeypatch.setattr(pm.config, "state_dir", lambda: str(tmp_path))
    assert pm.vip_cutover_baseline() == 1
    pm.push_vip_tfrs()
    assert sent == []                                                   # silenced at cutover
    rows.append(_row("6/9999", "ZDC", "WASHINGTON, DC", state="DC"))    # appears after cutover
    pm.push_vip_tfrs()
    assert [m.split("\n")[0] for topic, m in sent if topic == "tfr-alert"] == ["VIP TFR 6/9999: WASHINGTON, DC"]
    assert pm.vip_cutover_baseline() == 0                               # once, ever


def test_route_impact_gets_dc_vip_text_and_window_and_no_weather(monkeypatch):
    import poller.skills.route_impact as ri, poller.skills.tfr_enrichment as te
    rows = [{"tfr_id": "6/1", "is_vip": 1, "effective_start": 1800, "effective_end": 3600,
             "raw_json": json.dumps({"facility": "ZDC", "description": "THURMONT, MD, Friday"})},
            {"tfr_id": "6/2", "is_vip": 1, "effective_start": 1800, "effective_end": 3600,
             "raw_json": json.dumps({"facility": "ZAN", "description": "Anchorage, AK"})}]
    monkeypatch.setattr(ri.db, "get_active_tfrs", lambda: rows)
    monkeypatch.setattr(ri.db, "get_active_nas_programs", lambda: [])
    msg = ri._vip_user_message(ri.build_inputs())
    assert "VIP TFR 6/1: THURMONT, MD, Friday (1970-01-01 00:30Z / 19:30 ET to 1970-01-01 01:00Z / 20:00 ET)" in msg
    assert "Anchorage" not in msg and "METAR" not in msg and "ceiling" not in msg
    monkeypatch.setattr(te.db, "get_active_tfrs", lambda: rows)
    monkeypatch.setattr(te.db, "get_metar_snapshot", lambda: [])
    monkeypatch.setattr(te.db, "get_active_nas_programs", lambda: [])
    assert not any(t["vip"] for t in te.build_inputs()["tfr_ids"])        # no weather-laden VIP narrative


def test_route_hot_alert_fires_for_a_dc_vip_and_dedups_on_the_tfr_not_the_narrative(monkeypatch):
    import poller.skills.route_impact as ri
    rows = [{"tfr_id": "6/1", "is_vip": 1, "effective_start": 1800, "effective_end": 3600,
             "raw_json": json.dumps({"facility": "ZDC", "description": "THURMONT, MD"})}]
    sent, store = [], {}
    monkeypatch.setattr(ri.db, "get_active_tfrs", lambda: rows)
    monkeypatch.setattr(ri.db, "get_active_nas_programs", lambda: [])
    monkeypatch.setattr(ri.db, "insert_route_narrative", lambda *a, **k: None)
    monkeypatch.setattr(ri, "check_gate", lambda *a, **k: ("run", "h"))
    monkeypatch.setattr(ri, "commit_gate", lambda *a, **k: None)
    monkeypatch.setattr(ri, "log_usage", lambda *a, **k: None)
    narr = iter(["I-270 N and US-15 close 1400-1500Z for a Camp David movement.",
                 "Regenerated wording, same movement."])
    monkeypatch.setattr(ri, "_call_ollama_vip", lambda inputs: next(narr))
    monkeypatch.setattr(ri._ntfy, "send", lambda topic, text, **k: sent.append((topic, k.get("priority"), text)))
    monkeypatch.setattr(ri._route_dedup, "should_push", lambda k, h: store.get(k) != h)
    monkeypatch.setattr(ri._route_dedup, "record", lambda k, h: store.__setitem__(k, h))
    ri.main(); ri.main()
    assert len(sent) == 1 and sent[0][:2] == ("hot-alerts", 5)
    assert sent[0][2] == ("6/1 1970-01-01 00:30Z / 19:30 ET to 1970-01-01 01:00Z / 20:00 ET\n\n"
                          "I-270 N and US-15 close 1400-1500Z for a Camp David movement.")


def test_crane_sightings_are_not_labelled_marine_one():
    assert la._vip_local_kind("CRANE01", None).startswith("CRANE")
    assert la._vip_local_kind("MARINE1", None) == "Marine One"
    assert la._vip_local_kind("VENUS", None) == "VIP callsign VENUS"
