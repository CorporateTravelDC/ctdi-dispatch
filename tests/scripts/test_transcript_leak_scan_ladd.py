"""2026-10-19 (operator, Gate-1b): the transcript leak scan learns the LADD list as a
pattern class ('ladd-identifier').

Synthetic identifiers ONLY: the list is a monkeypatched loader of synthetic Q marks
(the demo scrub's Q + 5 shape, e.g. Q1ZT7) plus synthetic N-form / registry-form /
callsign-shaped entries built from Q-style characters. No real identifier appears here
and the real database is never touched."""
from __future__ import annotations

import importlib.util
import json
import os
import random
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ALPH = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
NFORM = "N7QZK"         # synthetic N-form entry (matched as N7QZK and 7QZK)
BAREFORM = "8QZK"       # synthetic registry-form entry (matched as 8QZK and N8QZK)
CALLSIGN = "QQZ901"     # synthetic flight-ID entry
HYPH = "Q-ZZTQ"         # synthetic hyphenated mark


def _synthetic_list(n=1200):
    r = random.Random(7)
    marks = {"Q1ZT7", NFORM, BAREFORM, CALLSIGN, HYPH, "12345"}
    while len(marks) < n:
        marks.add("Q" + "".join(r.choice(ALPH) for _ in range(5)))
    return sorted(marks)


@pytest.fixture
def m(monkeypatch, tmp_path):
    env = tmp_path / "secrets.env"
    env.write_text("")
    monkeypatch.setenv("LEAK_GUARD_SECRETS_ENV", str(env))
    monkeypatch.setenv("LEAK_GUARD_EXTRA_FILES", str(tmp_path / "none"))
    monkeypatch.setenv("LEAK_GUARD_SOURCES", "")
    # "run with the dispatch env" = the Postgres settings common.db.conn() uses (2026-10-20)
    monkeypatch.setenv("DISPATCH_PG_HOST", str(tmp_path / "no-socket"))
    monkeypatch.setenv("DISPATCH_PG_DB", "synthetic")
    monkeypatch.setenv("DISPATCH_PG_USER", "synthetic")
    spec = importlib.util.spec_from_file_location("tls_ladd", REPO / "scripts/leak-guard/transcript_leak_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_ladd_from_db", _synthetic_list)
    monkeypatch.setattr(mod, "_ladd_allow", lambda: set())
    return mod


def _old(p: Path):
    t = time.time() - 3600
    os.utime(p, (t, t))


def _root(tmp_path, **files):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)
        _old(root / name)
    return root


def test_forms_n_and_registry_and_callsign_whole_tokens_only(m):
    ladd, why = m.load_ladd_identifiers()
    assert why == "" and ladd
    hit = lambda s: len(m.ladd_spans(s.encode(), ladd, set()))
    assert hit("tail N7QZK seen") == 1 and hit("registry 7QZK") == 1          # N-form + bare form
    assert hit("as 8QZK and N8QZK") == 2
    assert hit("flight QQZ901 landed") == 1 and hit("mark Q-ZZTQ") == 1
    assert hit("part of foo-Q1ZT7-bar") == 1                                  # the tripwire's part rule
    assert hit("X-Q-ZZTQ") == 0                                               # hyphenated entry: whole token only
    assert hit("aQ1ZT7 Q1ZT7b q1zt7 QQZ9010") == 0                           # glued / lower case / longer
    assert hit("12345 and " + "N" + "12345") == 1                             # all-digit form never matched
    assert hit(json.dumps({"t": "line\nQ1ZT7\tQQZ901"})) == 2                 # JSON escapes are separators
    assert hit("\\u0022Q1ZT7\\u0022") == 1
    assert hit("A" * 250 + "+Q1ZT7/" + "b" * 250) == 0                        # inside a base64 payload


def test_scan_reports_counts_per_file_with_the_class_and_never_the_value(m, tmp_path, capsys):
    root = _root(tmp_path, **{"a.jsonl": json.dumps({"out": "Q1ZT7 then N7QZK then QQZ901"}) + "\n",
                              "clean.jsonl": json.dumps({"out": "nothing listed: Q1ZT8 n7qzk"}) + "\n"})
    rc = m.main(["scan", "--root", str(root)])
    out = capsys.readouterr().out
    assert rc == 1 and "a.jsonl: ladd-identifier x3" in out and "clean.jsonl" not in out
    for v in ("Q1ZT7", "N7QZK", "7QZK", "QQZ901"):
        assert v not in out


def test_redact_uses_the_atomic_path_and_proves_zero(m, tmp_path, capsys):
    root = _root(tmp_path, **{"s.jsonl": json.dumps({"out": "seen N7QZK at\nQ1ZT7"}) + "\n"})
    f = root / "s.jsonl"
    f.chmod(0o600)
    _old(f)
    ino = f.stat().st_ino
    assert m.main(["redact", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "PROOF: 1 file(s) redacted; rescan hits = 0" in out
    text = f.read_text()
    assert json.loads(text)["out"] == "seen [ladd-redacted] at\n[ladd-redacted]"
    assert f.stat().st_mode & 0o777 == 0o600 and f.stat().st_ino != ino      # replaced, same mode
    assert m.main(["scan", "--root", str(root)]) == 0
    assert "LADD class checked" in capsys.readouterr().out


def test_live_file_rule_applies_to_the_ladd_class(m, tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    live = root / "live.jsonl"
    live.write_text("Q1ZT7\n")                                                # written just now
    assert m.main(["redact", "--root", str(root)]) == 0
    assert "SKIPPED" in capsys.readouterr().out and live.read_text() == "Q1ZT7\n"
    assert m.main(["scan", "--root", str(root)]) == 0                         # live: warning only
    assert m.main(["redact", "--root", str(root), "--include-live"]) == 0
    assert live.read_text() == "[ladd-redacted]\n"


def test_db_unreachable_is_loud_and_not_a_pass(m, tmp_path, monkeypatch, capsys):
    def boom():
        raise ConnectionError("db down Q1ZT7")                                # the message is never printed
    monkeypatch.setattr(m, "_ladd_from_db", boom)
    root = _root(tmp_path, **{"c.jsonl": "nothing here\n"})
    assert m.main(["scan", "--root", str(root)]) == 3
    out = capsys.readouterr().out
    assert "ERROR LADD class 'ladd-identifier' NOT CHECKED" in out and "ConnectionError" in out
    assert "Q1ZT7" not in out and "clean (" not in out
    assert m.main(["redact", "--root", str(root)]) == 3
    assert "NOT CHECKED" in capsys.readouterr().out


def test_too_few_identifiers_fail_closed(m, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(m, "_ladd_from_db", lambda: ["Q1ZT7"])
    root = _root(tmp_path, **{"c.jsonl": "Q1ZT7\n"})
    assert m.main(["scan", "--root", str(root)]) == 3
    assert "fail closed" in capsys.readouterr().out


def test_without_the_dispatch_env_it_warns_and_require_ladd_fails(m, tmp_path, monkeypatch, capsys):
    for k in m.DISPATCH_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(m, "_ladd_from_db", lambda: pytest.fail("DB read without the dispatch env"))
    root = _root(tmp_path, **{"c.jsonl": "nothing\n"})
    assert m.main(["scan", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "WARNING LADD class 'ladd-identifier' NOT CHECKED: no dispatch env" in out
    assert m.main(["scan", "--root", str(root), "--require-ladd"]) == 3


def test_allow_list_hash_skips_a_confirmed_coincidence(m):
    import hashlib
    ladd, _ = m.load_ladd_identifiers()
    allow = {hashlib.sha256(b"Q1ZT7").hexdigest()}
    assert m.ladd_spans(b"Q1ZT7 QQZ901", ladd, allow) == [(6, 12)]
