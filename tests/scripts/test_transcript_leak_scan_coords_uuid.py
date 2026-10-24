"""2026-10-22 (operator decisions): the transcript leak scan

  * no longer treats feeder UUIDs (ultrafeeder UUID, piaware FEEDER_ID) as secret values,
    while sharing keys, FR24KEY, passwords and the OpenSky serial stay values;
  * learns the receiver-coords class: the receiver's own latitude / longitude, matched by
    VALUE (3 and 4 places, rounded or truncated, any sign / degree form), redacted to
    [coords-redacted]; NOT CHECKED (loud) when no value is readable;
  * stops the LADD class hitting ISO-8601 timestamp fractions and capitalised prose: the
    forms without an N-prefix match only in identifier context.

Synthetic values only: coordinates in open ocean, identifiers in the Q-registry style
(N0... never exists), UUIDs / keys built at runtime. No real file or database is read."""
from __future__ import annotations

import importlib.util
import json
import os
import random
import string
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/leak-guard/transcript_leak_scan.py"
RNG = random.Random(20261022)
LAT, LON = "12.34567", "-123.45678"          # synthetic: open Pacific, not a receiver


def rnd(n, alphabet=string.ascii_letters + string.digits):
    return "".join(RNG.choice(alphabet) for _ in range(n))


def load(monkeypatch, tmp_path, sources="", coord_sources=""):
    env = tmp_path / "dispatch-secrets.env"
    if not env.exists():
        env.write_text("")
    monkeypatch.setenv("LEAK_GUARD_SECRETS_ENV", str(env))
    monkeypatch.setenv("LEAK_GUARD_EXTRA_FILES", str(tmp_path / "none"))
    monkeypatch.setenv("LEAK_GUARD_SOURCES", sources)
    monkeypatch.setenv("LEAK_GUARD_COORD_SOURCES", coord_sources)
    for k in ("DISPATCH_PG_HOST", "DISPATCH_PG_DB", "DISPATCH_PG_USER"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("tls_1022", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _old(p: Path):
    t = time.time() - 3600
    os.utime(p, (t, t))


# ── 1. feeder UUIDs are public; real secrets stay values ──────────────────────

def test_feeder_uuids_are_not_values_but_keys_and_serial_are(monkeypatch, tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    uf_uuid, pa_id, d_uuid = str(uuid.UUID(int=RNG.getrandbits(128))), \
        str(uuid.UUID(int=RNG.getrandbits(128))), str(uuid.UUID(int=RNG.getrandbits(128)))
    fr24, share, serial, pw = rnd(16), rnd(20), "-" + rnd(10, string.digits), rnd(24)
    (etc / "ultrafeeder-secrets.env").write_text(f"READSB_LAT={LAT}\nREADSB_LON={LON}\nUUID={uf_uuid}\n")
    (etc / "piaware-secrets.env").write_text(f"FEEDER_ID={pa_id}\nLAT={LAT}\nLONG={LON}\n")
    (etc / "fr24feed-secrets.env").write_text(f"FR24KEY={fr24}\n")
    (etc / "airnavradar-secrets.env").write_text(f"SHARING_KEY={share}\n")
    (etc / "opensky-secrets.env").write_text(f"OPENSKY_USERNAME=someone\nOPENSKY_SERIAL={serial}\n")
    (tmp_path / "dispatch-secrets.env").write_text(
        f"ULTRAFEEDER_UUID={d_uuid}\nPIAWARE_FEEDER_ID={pa_id}\nUUID={uf_uuid}\nFEEDER_ID={pa_id}\n"
        f"SWIM_PASSWORD={pw}\n")
    m = load(monkeypatch, tmp_path, f"{etc}/*secrets*.env")
    vals = m.load_secret_values(tmp_path / "dispatch-secrets.env")
    flat = {f for forms in vals.values() for f in forms}
    for u in (uf_uuid, pa_id, d_uuid):
        assert u.encode() not in flat
    for v in (fr24, share, serial, pw):
        assert v.encode() in flat
    assert "opensky-secrets.env:OPENSKY_SERIAL" in vals
    blob = f"feeder {uf_uuid} / {pa_id} key {fr24}".encode()
    out = m.redact_blob(blob, vals)
    assert uf_uuid.encode() in out and pa_id.encode() in out and fr24.encode() not in out


# ── 2. receiver coordinates ───────────────────────────────────────────────────

def _coords_file(tmp_path, text=f"READSB_LAT={LAT}\nREADSB_LON={LON}\nUUID=x\n"):
    f = tmp_path / "ultrafeeder-secrets.env"
    f.write_text(text)
    return f"{f}|READSB_LAT,READSB_LON"


@pytest.mark.parametrize("text", [
    f"--lat {LAT} --lon {LON}",
    "LAT=12.3457 LONG=-123.4568",
    f"READSB_LAT={LAT}",
    "receiver at +12.3457°, -123.4568°",
    "12.346, -123.457",
    "12.345,-123.456",                                        # truncated to 3 places
    json.dumps({"out": "pos +12.3457° / -123.4568°"}),   # JSON-escaped degree sign
])
def test_coordinate_forms_are_redacted(monkeypatch, tmp_path, text):
    m = load(monkeypatch, tmp_path, coord_sources=_coords_file(tmp_path))
    coords, why = m.load_receiver_coords()
    assert why == "" and coords
    out = m.redact_blob(text.encode(), {}, None, coords)
    assert out.count(b"[coords-redacted]") >= 1
    for frag in (b"12.34", b"123.45"):
        assert frag not in out
    assert b"\xc2\xb0" not in out and b"\\u00b0" not in out
    assert m.find(out, {}, None, coords) == {}


def test_unrelated_numbers_are_untouched(monkeypatch, tmp_path):
    m = load(monkeypatch, tmp_path, coord_sources=_coords_file(tmp_path))
    coords, _ = m.load_receiver_coords()
    text = (b"v12.34 at 12.35 and 12.399, 112.3456, 12.3456789.1, 99.34567, -77.45678, "
            b"123.4, 2026-10-22T13:02:19.536Z, 13:12.345")
    hits = m.coord_spans(text, coords)
    assert [text[a:b] for a, b in hits] == [b"12.345"]       # only the value at 3 places (the last one)
    assert m.coord_spans(b"v12.34 at 12.35 and 12.399 and 112.3456", coords) == []


def test_scan_reports_class_and_never_the_value(monkeypatch, tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "t.jsonl"
    f.write_text(json.dumps({"out": f"ultrafeeder --lat {LAT} --lon {LON}"}) + "\n")
    _old(f)
    m = load(monkeypatch, tmp_path, coord_sources=_coords_file(tmp_path))
    assert m.main(["scan", "--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "t.jsonl: receiver-coords x2" in out and LAT not in out and LON not in out
    assert m.main(["redact", "--root", str(root)]) == 0
    assert "rescan hits = 0" in capsys.readouterr().out
    assert json.loads(f.read_text())["out"] == "ultrafeeder --lat [coords-redacted] --lon [coords-redacted]"


def test_unreadable_coords_are_not_checked_loudly(monkeypatch, tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "c.jsonl").write_text("nothing\n")
    _old(root / "c.jsonl")
    m = load(monkeypatch, tmp_path, coord_sources=f"{tmp_path / 'missing.env'}|READSB_LAT,READSB_LON")
    assert m.main(["scan", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "WARNING class 'receiver-coords' NOT CHECKED" in out and "missing.env" in out
    assert "clean (" not in out
    assert m.main(["scan", "--root", str(root), "--require-coords"]) == 3
    assert "ERROR class 'receiver-coords' NOT CHECKED" in capsys.readouterr().out


# ── 3. LADD false positives ───────────────────────────────────────────────────

TS = "2026-10-22T13:02:19.536Z"                               # the operator's exact false positive
PROSE = "unplanned Nextcloud MAJOR VERSION jump"              # ditto


def _ladd_mod(monkeypatch, tmp_path, extra=()):
    m = load(monkeypatch, tmp_path)
    alph = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    r = random.Random(7)
    marks = {"N7QZK", "8QZK", "QQZ901", "N019Z", *extra}       # N0... never exists: synthetic
    while len(marks) < 1200:
        marks.add("Q" + "".join(r.choice(alph) for _ in range(5)))
    ladd, why = m._ladd_floor(sorted(marks))
    assert why == ""
    return m, ladd


def test_timestamp_fraction_and_prose_words_are_untouched(monkeypatch, tmp_path):
    # Every candidate the old rule could hit is put on the list at runtime: the fraction of
    # the timestamp and the capitalised prose words. None of them may be redacted.
    frac = TS.rsplit(".", 1)[1]
    words = [w for w in PROSE.split() if w.isupper()]
    m, ladd = _ladd_mod(monkeypatch, tmp_path, extra=[frac, *words])
    for text in (TS, f'{{"timestamp": "{TS}"}}', PROSE, json.dumps({"out": PROSE + "\n" + TS}),
                 "2026-10-22T13:02:19.019Z", "at 13:019Z"):
        assert m.ladd_spans(text.encode(), ladd, set()) == [], text
        assert m.redact_ladd(text.encode(), ladd) == text.encode()


def test_real_n_number_and_registry_form_in_context_still_redacted(monkeypatch, tmp_path):
    m, ladd = _ladd_mod(monkeypatch, tmp_path, extra=["QZXQV"])
    red = lambda s: m.redact_ladd(s.encode(), ladd).decode()
    assert red("spotted N7QZK overhead") == "spotted [ladd-redacted] overhead"            # N-form, no context
    assert red("registration: 8QZK") == "registration: [ladd-redacted]"
    assert red('{"hex":"a1b2c3","r":"8QZK"}') == '{"hex":"a1b2c3","r":"[ladd-redacted]"}'
    assert red(json.dumps({"registration": "019Z"})) == json.dumps({"registration": "[ladd-redacted]"})
    assert red("tail number 7QZK, reg=8QZK") == "tail number [ladd-redacted], reg=[ladd-redacted]"
    assert red("callsign QZXQV") == "callsign [ladd-redacted]"                          # letters-only: context
    assert red("the QZXQV word") == "the QZXQV word"
    assert red("as 8QZK in prose") == "as 8QZK in prose"
    assert red("flight QQZ901 landed") == "flight [ladd-redacted] landed"
