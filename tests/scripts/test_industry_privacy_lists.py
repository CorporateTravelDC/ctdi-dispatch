"""2026-10-23 (operator): the privacy checks are industry aware.

An aviation list matches only in aviation identifier context, a maritime list only in
vessel fields / context, ground and rail only after their own keys; never cross-match
(operator correction 2026-10-23: not even an N-number inside a vessel field); every
hit reports its industry; a match in any vertical blocks; a missing or empty list is
"no entries", never a failure in itself -- but a suspected identifier in that vertical's
own fields is held for human review (--review, terminal only) unless its hash is in the
review allow file.

Synthetic identifiers only (tests/scripts/test_synthetic_identifiers.py): every aviation
entry is a Q-registry value or assembled at runtime, MMSI / IMO values are obviously
fake (leading zeros), and lists are written to tmp_path via CTDC_PRIVACY_LIST_DIR --
no real list or database is read."""
from __future__ import annotations

import importlib.util
import io
import os
import random
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LG = REPO / "scripts/leak-guard"
RNG = random.Random(20261023)

LETTERS = "QZQZQ"                                   # a letters-only aviation entry
NFORM = "N" + "7" + "0" + "QX"[::-1]                # an N-form, assembled at runtime
MMSI = "000" + "000" + "417"                        # 9 digits, fake (no MID 000)
IMO = "000" + "0417"                                # 7 digits, fake
FILL = sorted({"Q" + "".join(RNG.choice("ABCDEFGHJKLMNPRSTUVWXYZ0123456789") for _ in range(5))
               for _ in range(1100)})
AVIATION = [LETTERS, NFORM] + FILL

AIS_CSV = ("MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,VesselType\n"
           f"{MMSI},2024-01-01T10:00:00,38.85,-77.04,3.1,90.0,511,{LETTERS},60\n"
           "000000418,2024-01-01T10:00:00,38.85,-77.04,1,1,1,PADDED,30\n")


def _load(name):
    spec = importlib.util.spec_from_file_location(f"t_{name}", LG / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m                      # dataclasses resolve annotations through it
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def lists_dir(tmp_path, monkeypatch):
    d = tmp_path / "etc"
    d.mkdir()
    monkeypatch.setenv("CTDC_PRIVACY_LIST_DIR", str(d))
    return d


GID, RID, RID2, GID2 = "QQ-SYN-" + "0001", "QRSYN" + "9", "QR" + "7001", "QQ-" + "0001"   # runtime-assembled
OTHER = "mmsi:" + "000000999"                          # an unrelated entry: maritime "checked"


def _maritime(d: Path, *lines):
    (d / "privacy-maritime.list").write_text("# synthetic\n" + "\n".join(lines) + "\n")


# ── public tripwire ──────────────────────────────────────────────────────────────
def _public(monkeypatch, tree, idents=AVIATION, suspect_allow=None, review=False):
    m = _load("ladd_public_check")
    monkeypatch.setattr(m, "load_allow", lambda: set())
    monkeypatch.setattr(m, "SUSPECT_ALLOW", suspect_allow or tree.parent / "no-review-allow")
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(idents) + "\n"))
    return m.main(["--tree", str(tree)] + (["--review"] if review else []))


def _tree(tmp_path, **files):
    t = tmp_path / "tree"
    t.mkdir(exist_ok=True)
    for name, text in files.items():
        (t / name.replace("__", ".")).write_text(text)
    return t


def test_public_vesselname_equal_to_letters_only_entry_is_not_aviation(monkeypatch, tmp_path, lists_dir, capsys):
    _maritime(lists_dir, OTHER)
    tree = _tree(tmp_path, ais__csv=AIS_CSV,
                 fixture__py=f'csv = ("MMSI,LAT,LON,VesselName\\n"\n       "{MMSI},38.8,-77.0,{LETTERS}\\n")\n')
    assert _public(monkeypatch, tree) == 0
    out = capsys.readouterr().out
    assert "clean (" in out and "maritime: 1 entry" in out
    assert LETTERS not in out and MMSI not in out


def test_public_same_token_after_registration_is_aviation(monkeypatch, tmp_path, lists_dir, capsys):
    _maritime(lists_dir, OTHER)
    tree = _tree(tmp_path, ais__csv=AIS_CSV, note__md=f"intro\nregistration: {LETTERS}\n")
    assert _public(monkeypatch, tree) == 1
    out = capsys.readouterr().out
    assert "note.md: line(s) 2 [aviation: ladd-identifier]" in out
    assert "ais.csv" not in out and LETTERS not in out


def test_public_maritime_mmsi_in_field_blocks_and_bare_number_does_not(monkeypatch, tmp_path, lists_dir, capsys):
    _maritime(lists_dir, f"mmsi:{MMSI}", f"imo:{IMO}")
    tree = _tree(tmp_path, ais__csv=AIS_CSV,
                 rec__json=f'{{"mmsi": {MMSI}, "speed": 3}}\n',
                 imo__md=f"line one\nthe ship is IMO {IMO} today\n",
                 other__txt=f"invoice total {MMSI} and order {IMO}\nid={MMSI}\n")
    assert _public(monkeypatch, tree) == 1
    out = capsys.readouterr().out
    assert "ais.csv: line(s) 2 [maritime: maritime-identifier]" in out
    assert "rec.json: line(s) 1 [maritime: maritime-identifier]" in out
    assert "imo.md: line(s) 2 [maritime: maritime-identifier]" in out
    assert "other.txt" not in out and MMSI not in out and IMO not in out
    assert "maritime: 2 entries" in out


def test_public_nform_inside_vessel_field_is_not_aviation(monkeypatch, tmp_path, lists_dir, capsys):
    """Operator correction 2026-10-23: strictly no cross-vertical matching. An N-shaped
    VesselName (CSV column or vessel_name key) is a vessel name, not an aircraft; the
    same N-form anywhere outside another vertical's fields is still aviation."""
    _maritime(lists_dir, OTHER)
    csv = AIS_CSV.replace(f",{LETTERS},", f",{NFORM},")
    tree = _tree(tmp_path, ais__csv=csv, rec__json=f'{{"vessel_name": "{NFORM}"}}\n')
    assert _public(monkeypatch, tree) == 0
    capsys.readouterr()
    (tree / "prose.md").write_text(f"spotted {NFORM} on final\n")
    assert _public(monkeypatch, tree) == 1
    out = capsys.readouterr().out
    assert "prose.md: line(s) 1 [aviation: ladd-identifier]" in out
    assert "ais.csv" not in out and "rec.json" not in out and NFORM not in out


def test_public_empty_or_missing_maritime_list_is_no_entries(monkeypatch, tmp_path, lists_dir, capsys):
    tree = _tree(tmp_path, a__txt="nothing here\n")
    assert _public(monkeypatch, tree) == 0                      # missing file
    assert "maritime: no entries" in capsys.readouterr().out
    _maritime(lists_dir)                                         # comments only
    assert _public(monkeypatch, tree) == 0
    out = capsys.readouterr().out
    assert "maritime: no entries" in out and "ground: no entries" in out and "rail: no entries" in out


def test_public_aviation_still_fails_closed(monkeypatch, tmp_path, lists_dir, capsys):
    _maritime(lists_dir, f"mmsi:{MMSI}")
    tree = _tree(tmp_path, a__txt="nothing\n")
    assert _public(monkeypatch, tree, idents=FILL[:10]) == 2
    assert "FAIL CLOSED" in capsys.readouterr().out


def test_public_ground_and_rail_only_in_their_own_keys(monkeypatch, tmp_path, lists_dir, capsys):
    (lists_dir / "privacy-ground.list").write_text(f"id:{GID}\n")
    (lists_dir / "privacy-rail.list").write_text(f"id:{RID}\n")
    tree = _tree(tmp_path, g__md=f"vehicle_id: {GID}\n", r__md=f"x\ntrain {RID} late\n",
                 n__md=f"code {GID} and {RID} elsewhere\n")
    assert _public(monkeypatch, tree) == 1
    out = capsys.readouterr().out
    assert "g.md: line(s) 1 [ground: ground-identifier]" in out
    assert "r.md: line(s) 2 [rail: rail-identifier]" in out
    assert "n.md" not in out and RID not in out


def test_unreadable_list_is_reported_not_failed(monkeypatch, tmp_path, lists_dir, capsys):
    if os.geteuid() == 0:
        pytest.skip("root reads anything")
    p = lists_dir / "privacy-maritime.list"
    p.write_text(f"mmsi:{MMSI}\n")
    p.chmod(0)
    try:
        assert _public(monkeypatch, _tree(tmp_path, a__txt="x\n")) == 0
        assert "maritime: NOT CHECKED (list unreadable: PermissionError)" in capsys.readouterr().out
    finally:
        p.chmod(0o600)


def test_malformed_lines_counted_never_echoed(lists_dir):
    pl = _load("privacy_lists")
    _maritime(lists_dir, "mmsi:12345", "imo:abc", "bogus line", f"name:SYNTHETIC VESSEL QQ")
    kl = pl.load_keyed_list(pl.MARITIME)
    assert kl.count == 1 and "3 malformed line(s) ignored" in kl.status and "12345" not in kl.status


# ── transcript scan ──────────────────────────────────────────────────────────────
def _tls(monkeypatch, tmp_path):
    env = tmp_path / "dispatch-secrets.env"
    env.write_text("")
    monkeypatch.setenv("LEAK_GUARD_SECRETS_ENV", str(env))
    monkeypatch.setenv("LEAK_GUARD_EXTRA_FILES", str(tmp_path / "none"))
    monkeypatch.setenv("LEAK_GUARD_SOURCES", "")
    for k in ("DISPATCH_PG_HOST", "DISPATCH_PG_DB", "DISPATCH_PG_USER"):
        monkeypatch.delenv(k, raising=False)
    m = _load("transcript_leak_scan")
    monkeypatch.setattr(m, "_ladd_allow", lambda: set())
    return m


def _scan(m, monkeypatch, tmp_path, text, cmd="scan"):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    f = root / "t.jsonl"
    f.write_text(text)
    t = time.time() - 3600
    os.utime(f, (t, t))
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO("\n".join(AVIATION).encode())))
    return m.main([cmd, "--root", str(root), "--ladd-stdin"]), f


def test_transcript_vesselname_not_aviation_registration_is(monkeypatch, tmp_path, lists_dir, capsys):
    m = _tls(monkeypatch, tmp_path)
    rc, _ = _scan(m, monkeypatch, tmp_path, '{"out": "' + AIS_CSV.replace("\n", "\\n") + '"}\n')
    out = capsys.readouterr().out
    assert rc == 0 and "maritime: no entries" in out
    rc, _ = _scan(m, monkeypatch, tmp_path, '{"out": "registration: ' + LETTERS + '"}\n')
    out = capsys.readouterr().out
    assert rc == 1 and "ladd-identifier x1 [aviation]" in out and LETTERS not in out


def test_transcript_maritime_hit_blocks_and_redacts(monkeypatch, tmp_path, lists_dir, capsys):
    _maritime(lists_dir, f"mmsi:{MMSI}")
    m = _tls(monkeypatch, tmp_path)
    text = '{"out": "MMSI ' + MMSI + ' seen; total ' + MMSI + ' units"}\n'
    rc, f = _scan(m, monkeypatch, tmp_path, text)
    out = capsys.readouterr().out
    assert rc == 1 and "maritime-identifier x1 [maritime]" in out and MMSI not in out
    rc, f = _scan(m, monkeypatch, tmp_path, text, cmd="redact")
    body = f.read_text()
    assert rc == 0 and "[maritime-redacted]" in body and body.count(MMSI) == 1   # the bare number stays


def test_transcript_nform_in_vessel_field_not_flagged(monkeypatch, tmp_path, lists_dir):
    m = _tls(monkeypatch, tmp_path)
    forms = m.ladd_forms(AVIATION)
    assert m.find(f'{{"mmsi": "{MMSI}", "VesselName": "{NFORM}"}}'.encode(), {}, forms) == {}
    assert m.find(f"saw {NFORM} taxi".encode(), {}, forms) == {"ladd-identifier": 1}


def test_ais_records_never_cross_match_aviation_callsign(lists_dir):
    """An AIS CallSign (callsign key inside a record carrying an mmsi key) is maritime."""
    pl = _load("privacy_lists")
    forms = pl.aviation_forms(AVIATION)
    rec = f'{{"mmsi": "{MMSI}", "callsign": "{LETTERS}"}}'.encode()
    assert pl.aviation_spans(rec, forms, set()) == []
    assert pl.aviation_spans(f'{{"callsign": "{LETTERS}"}}'.encode(), forms, set()) != []


def test_semantic_ais_fixture_passes_the_public_check(monkeypatch, tmp_path, lists_dir, capsys):
    """tests/second_brain/test_semantic.py's AIS seed fixture, with its sample VesselName
    values injected into the aviation list as letters-only entries: never aviation. With
    a maritime list it is clean; with none, its AIS fields are held for review until the
    operator allowlists them by hash."""
    pl = _load("privacy_lists")
    src = (REPO / "tests/second_brain/test_semantic.py").read_text()
    tree = _tree(tmp_path)
    (tree / "test_semantic.py").write_text(src)
    idents = ["TEST", "PADDED", "SYNTHETICVESSELTWO"] + FILL
    _maritime(lists_dir, OTHER)
    assert _public(monkeypatch, tree, idents=idents) == 0
    assert "clean (" in capsys.readouterr().out
    (lists_dir / "privacy-maritime.list").unlink()
    assert _public(monkeypatch, tree, idents=idents) == 1
    out = capsys.readouterr().out
    assert "HOLD" in out and "ladd-identifier" not in out
    blob = src.encode()
    lists = pl.PrivacyLists(None, "", pl.load_keyed_lists())
    allow = tmp_path / "review-allow.sha256"
    allow.write_text("".join(f"{pl.suspect_hash(x.industry, x.value(blob))}  synthetic fixture\n"
                             for x in pl.suspects(blob, lists, set())))
    assert _public(monkeypatch, tree, idents=idents, suspect_allow=allow) == 0


# ── suspected identifiers in a vertical with no list (operator, 2026-10-23) ──────
def test_suspected_mmsi_with_empty_list_holds_the_push_until_allowlisted(monkeypatch, tmp_path, lists_dir, capsys):
    pl = _load("privacy_lists")
    _maritime(lists_dir)                                          # empty list
    tree = _tree(tmp_path, ais__csv="MMSI,LAT,LON\n" + MMSI + ",38.8,-77.0\n",
                 other__txt=f"order {MMSI} shipped\n")             # bare number: not a field
    assert _public(monkeypatch, tree) == 1
    out = capsys.readouterr().out
    assert "maritime: no entries" in out and "HOLD" in out and "--review" in out
    assert "ais.csv: line(s) 2 [suspected maritime identifier -- no list, needs review]" in out
    assert "other.txt" not in out and MMSI not in out
    allow = tmp_path / "review-allow.sha256"
    allow.write_text(f"{pl.suspect_hash('maritime', MMSI.encode())}  synthetic test value\n")
    assert _public(monkeypatch, tree, suspect_allow=allow) == 0
    assert "clean (" in capsys.readouterr().out


def test_review_prints_suggested_entry_and_allow_line(monkeypatch, tmp_path, lists_dir, capsys):
    pl = _load("privacy_lists")
    tree = _tree(tmp_path, rec__json=f'{{"mmsi": "{MMSI}", "imo": "IMO{IMO}"}}\n')
    assert _public(monkeypatch, tree, review=True) == 1
    out = capsys.readouterr().out                               # operator terminal: values shown
    assert f"mmsi:{MMSI}  # seen in mmsi field at rec.json:1; suggested by review " in out
    assert f"imo:{IMO}  # seen in IMO prefix / field at rec.json:1" in out
    assert pl.suspect_hash("maritime", MMSI.encode()) in out
    assert not (lists_dir / "privacy-maritime.list").exists()    # never auto-appended


def test_suspected_rail_and_ground_ids_only_in_fields(monkeypatch, tmp_path, lists_dir, capsys):
    tree = _tree(tmp_path, r__yaml=f"train_id: {RID2}\n", g__md=f"license_plate = {GID2}\n",
                 prose__md=f"the train {RID2} and the vehicle {GID2} in prose\n")
    assert _public(monkeypatch, tree) == 1
    out = capsys.readouterr().out
    assert "r.yaml: line(s) 1 [suspected rail identifier" in out
    assert "g.md: line(s) 1 [suspected ground identifier" in out and "prose.md" not in out


def test_transcript_reports_suspects_by_path_only_exit_unaffected(monkeypatch, tmp_path, lists_dir, capsys):
    m = _tls(monkeypatch, tmp_path)
    rc, _ = _scan(m, monkeypatch, tmp_path, '{"out": "mmsi: ' + MMSI + '"}\n')
    out = capsys.readouterr().out
    assert rc == 0 and "suspected-maritime-identifier x1" in out and "needs review" in out
    assert MMSI not in out
