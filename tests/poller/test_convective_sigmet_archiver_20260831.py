"""
Tests for the 2026-08-31 convective-SIGMET fetcher + archive (the
weather-history half Detector D was blocked on -- see the 2026-08-31
block in src/ingest/README.md).

Fixture provenance: tests/poller/fixtures/awc_airsigmet_live_20260831.json
is the VERBATIM, unmodified body of a real
https://aviationweather.gov/api/data/airsigmet?format=json response
fetched from this box on 2026-08-31 ~06:43 EDT -- 16 records, ALL
convective SIGMETs (KKCI series 38E-44E / 42W-45W / 30C-34C), epoch-int
validTimeFrom/To, 12 of 16 raw texts longer than the web overlay's
600-char truncation, 6 of 16 with null storm motion. No synthetic
records are used except where a malformed shape is the point of the
test (labeled inline).

Covers, in order:
  1. normalize_airsigmet() against every real record: field mapping,
     the <3-coord polygon guard, full-vs-truncated raw text.
  2. epoch_to_iso(): AWC epoch ints, ISO passthrough, None/garbage.
  3. fetch_convective()'s hazard filter, including the
     nothing-convective-right-now common case (network monkeypatched --
     the live endpoint was exercised for real when the fixture was
     captured; tests must pass offline).
  4. archive_convective_sigmets() against an isolated DB: insert-once
     per (sigmet_id, valid_from) with first_seen preserved on re-fetch,
     the series-number-recycle case (same composite id, new validity ->
     NEW row -- live-verified reality the id-alone key would corrupt),
     malformed-record non-fatality, and v47 idempotency.
  5. The no-prune guarantee: retention_prune's opt-in job list must
     never silently grow this table.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from common import airsigmet, db_swim  # noqa: E402
from common import db  # noqa: E402
from poller.skills import convective_sigmet_archiver as arch  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "awc_airsigmet_live_20260831.json"


def _raw_records() -> list[dict]:
    return json.loads(FIXTURE.read_text())


# ── 1. Normalization against the real capture ────────────────────────────────

def test_normalize_all_real_records():
    raws = _raw_records()
    assert len(raws) == 16
    for r in raws:
        n = airsigmet.normalize_airsigmet(r, raw_text_limit=None)
        assert n is not None
        assert n["hazard"] == "CONVECTIVE"
        assert n["type"] == "SIGMET"
        # Composite id shape: KKCI-<series>-<alpha>
        parts = n["id"].split("-")
        assert parts[0] == "KKCI" and len(parts) == 3
        assert len(n["coords"]) >= 3
        assert all(len(pt) == 2 for pt in n["coords"])
        # Live-verified: AWC serves validity as epoch ints, not ISO.
        assert isinstance(n["valid_from"], int)
        assert isinstance(n["valid_to"], int)
        assert n["raw_text"].startswith("WSUS3")


def test_normalize_raw_text_truncation_default_vs_archive():
    """The web overlay's historical 600-char cap is the default; the
    archiver passes None and must get the full product text (12 of the
    16 real records exceed 600 chars)."""
    raws = _raw_records()
    long_ones = [r for r in raws if len(r.get("rawAirSigmet") or "") > 600]
    assert len(long_ones) == 12
    r = long_ones[0]
    capped = airsigmet.normalize_airsigmet(r)
    full = airsigmet.normalize_airsigmet(r, raw_text_limit=None)
    assert len(capped["raw_text"]) == 600
    assert full["raw_text"] == r["rawAirSigmet"]


def test_normalize_drops_incomplete_polygon():
    """<3 coordinate points -> None (the guard web/main.py has always
    applied). Synthetic mutation of a real record -- degenerate shapes
    are the point here."""
    r = dict(_raw_records()[0])
    r["coords"] = r["coords"][:2]
    assert airsigmet.normalize_airsigmet(r) is None
    r["coords"] = []
    assert airsigmet.normalize_airsigmet(r) is None
    r.pop("coords")
    assert airsigmet.normalize_airsigmet(r) is None


def test_normalize_coords_missing_latlon_keys_skipped():
    """Synthetic: coord entries without lat/lon keys are dropped before
    the >=3 check, same as the web overlay."""
    r = dict(_raw_records()[0])
    r["coords"] = [{"lat": 1.0, "lon": 2.0}, {"bogus": True},
                   {"lat": 3.0, "lon": 4.0}]
    assert airsigmet.normalize_airsigmet(r) is None


# ── 2. epoch_to_iso ──────────────────────────────────────────────────────────

def test_epoch_to_iso_real_values():
    raws = _raw_records()
    iso = airsigmet.epoch_to_iso(raws[0]["validTimeFrom"])  # 1788170100
    assert iso == "2026-08-31T09:55:00Z"


def test_epoch_to_iso_passthrough_and_garbage():
    assert airsigmet.epoch_to_iso("2026-08-31T09:55:00Z") == "2026-08-31T09:55:00Z"
    assert airsigmet.epoch_to_iso("1788170100") == "2026-08-31T09:55:00Z"
    assert airsigmet.epoch_to_iso(None) is None
    assert airsigmet.epoch_to_iso("") is None
    assert airsigmet.epoch_to_iso([1, 2]) is None


# ── 3. fetch_convective filter (network monkeypatched) ───────────────────────

def _normalized_fixture() -> list[dict]:
    return [airsigmet.normalize_airsigmet(r, raw_text_limit=None)
            for r in _raw_records()]


def test_fetch_convective_filters_hazards(monkeypatch):
    turb = dict(_normalized_fixture()[0])
    turb["hazard"] = "TURB"
    ifr = dict(_normalized_fixture()[0])
    ifr["hazard"] = "IFR"
    mixed = _normalized_fixture() + [turb, ifr]
    monkeypatch.setattr(airsigmet, "fetch_airsigmets",
                        lambda timeout, raw_text_limit: mixed)
    total, conv = arch.fetch_convective()
    assert total == 18
    assert len(conv) == 16
    assert all(c["hazard"] == "CONVECTIVE" for c in conv)


def test_fetch_convective_nothing_convective(monkeypatch):
    """The common case outside convective season/hours: AWC returns only
    non-convective products (or nothing at all). Must be a clean success
    with an empty list, never an error."""
    monkeypatch.setattr(airsigmet, "fetch_airsigmets",
                        lambda timeout, raw_text_limit: [])
    total, conv = arch.fetch_convective()
    assert (total, conv) == (0, [])


# ── 4. Archive semantics against an isolated DB ──────────────────────────────

def _isolated_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    orig = db._db_path
    db._db_path = lambda: Path(tmp.name)
    db_swim.init_db_swim_v47()
    return tmp, orig


def _rows(tmp):
    c = sqlite3.connect(tmp.name)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM convective_sigmet_archive ORDER BY id")]
    finally:
        c.close()


def test_archive_real_capture_insert_once():
    tmp, orig = _isolated_db()
    try:
        recs = _normalized_fixture()
        inserted, skipped = db_swim.archive_convective_sigmets(recs)
        assert (inserted, skipped) == (16, 0)
        first = _rows(tmp)
        assert len(first) == 16
        r0 = first[0]
        assert r0["sigmet_id"] == "KKCI-38E-E"
        assert r0["hazard"] == "CONVECTIVE"
        assert r0["valid_from"] == "2026-08-31T09:55:00Z"  # epoch -> ISO
        assert r0["valid_to"] == "2026-08-31T11:55:00Z"
        poly = json.loads(r0["polygon"])
        assert len(poly) >= 3 and len(poly[0]) == 2
        assert r0["raw_text"].startswith("WSUS31")
        assert r0["first_seen"]
        # Re-fetch of the same still-active set: insert-once, first_seen
        # untouched (INSERT OR IGNORE, never an upsert).
        inserted2, skipped2 = db_swim.archive_convective_sigmets(recs)
        assert (inserted2, skipped2) == (0, 0)
        assert _rows(tmp) == first
    finally:
        db._db_path = orig


def test_archive_series_recycle_is_a_new_row():
    """Live-verified 2026-08-31: convective SIGMET series numbers
    recycle, so tomorrow's KKCI-38E-E is a DIFFERENT storm. Same
    composite id + new valid_from must archive as a second row --
    keying on id alone would silently discard every day after the
    first."""
    tmp, orig = _isolated_db()
    try:
        rec = _normalized_fixture()[0]
        db_swim.archive_convective_sigmets([rec])
        tomorrow = dict(rec)
        tomorrow["valid_from"] = rec["valid_from"] + 86400
        tomorrow["valid_to"] = rec["valid_to"] + 86400
        inserted, _ = db_swim.archive_convective_sigmets([tomorrow])
        assert inserted == 1
        rows = _rows(tmp)
        assert len(rows) == 2
        assert rows[0]["sigmet_id"] == rows[1]["sigmet_id"] == "KKCI-38E-E"
        assert rows[0]["valid_from"] != rows[1]["valid_from"]
    finally:
        db._db_path = orig


def test_archive_malformed_record_nonfatal():
    """Synthetic: one record with a degenerate polygon must be skipped
    (counted) while the rest of the batch archives -- a production
    scheduled skill never aborts a batch over one bad record."""
    tmp, orig = _isolated_db()
    try:
        good = _normalized_fixture()[:3]
        bad = dict(good[0])
        bad["id"] = "KKCI-99X-X"
        bad["coords"] = [[1.0, 2.0]]
        inserted, skipped = db_swim.archive_convective_sigmets(
            [good[0], bad, good[1], good[2]])
        assert (inserted, skipped) == (3, 1)
        assert len(_rows(tmp)) == 3
    finally:
        db._db_path = orig


def test_init_db_swim_v47_is_idempotent():
    tmp, orig = _isolated_db()
    try:
        db_swim.init_db_swim_v47()
        db_swim.init_db_swim_v47()
        assert _rows(tmp) == []
    finally:
        db._db_path = orig


# ── 5. No-prune guarantee ────────────────────────────────────────────────────

def test_archive_not_in_retention_prune_jobs():
    """convective_sigmet_archive must accumulate history (attribution
    backtesting needs seasons of depth) -- it must never quietly join
    retention_prune's opt-in sweep. If this test fails, someone added
    it: that requires explicit operator sign-off, see the
    SCHEMA_SWIM_V47 comment block."""
    from poller.skills import retention_prune
    labels = " ".join(label for label, _ in retention_prune._PRUNE_JOBS)
    assert "convective_sigmet" not in labels
