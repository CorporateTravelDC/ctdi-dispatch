"""config/pihole/profiles/merged-blocklists/blocklist_merge.py (2026-11-27). Synthetic names only (RFC 2606 .test /
.example); no real list content."""
from __future__ import annotations

import gzip
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
D = REPO / "config/pihole/profiles/merged-blocklists"
spec = importlib.util.spec_from_file_location("blocklist_merge", D / "blocklist_merge.py")
bm = importlib.util.module_from_spec(spec); spec.loader.exec_module(bm)
NOW = 1_795_000_000.0
DAY = 86400
PSL_TEXT = "// fixture\ntest\nexample\nco.test\n*.wild.test\n!keep.wild.test\n// ===BEGIN PRIVATE DOMAINS===\nhost.test\n"


def conf(tmp_path, **kw):
    base = {"OUT_DIR": tmp_path / "out", "LIST_DIR": tmp_path / "lists", "CHECK_DIR": tmp_path / "check",
            "PIHOLE_LOG": tmp_path / "pihole.log", "MIN_ENTRIES": "3", "BLP_LISTS": "malware ads",
            "HAGEZI_LISTS": "tif", "READ_GROUP": __import__("grp").getgrgid(__import__("os").getgid()).gr_name}
    base.update(kw)
    return bm.parse_conf("\n".join(f"{k}={v}" for k, v in base.items()))


def test_example_config_parses_and_bad_values_are_refused(tmp_path):
    c = bm.parse_conf((D / "merge.conf.example").read_text())
    assert c["PRUNE_CONFIRM"] == 2 and "malware" in c["BLP_LISTS"] and c["PIHOLE_DNS"] == "127.0.0.1#53"
    for bad in ("PRUNE_CONFIRM=1", "UNBOUND_DNS=8.8.8.8#53", "BLP_URL=http://x.test/{}.txt", "NOPE=1", "LIST_DIR=rel/path"):
        with pytest.raises(bm.ConfError):
            bm.parse_conf(bad)


def test_every_list_format_parses():
    t = "# c\n0.0.0.0 a.bad.test\n127.0.0.1 b.bad.test # x\nc.bad.test\n*.d.bad.test\n||e.bad.test^\n0.0.0.0 localhost\njunk line\n"
    assert bm.domains(t) == {"a.bad.test", "b.bad.test", "c.bad.test", "d.bad.test", "e.bad.test"}


def test_registrable_follows_the_public_suffix_list():
    psl = bm.PSL(PSL_TEXT)
    assert psl.registrable("a.b.evil.test") == "evil.test"
    assert psl.registrable("x.evil.co.test") == "evil.co.test"
    assert psl.registrable("site.host.test") == "site.host.test"          # a private-section suffix
    assert psl.registrable("a.b.wild.test") == "a.b.wild.test" and psl.registrable("x.keep.wild.test") == "keep.wild.test"
    assert psl.registrable("co.test") == ""


def test_merge_covers_folds_guards_platforms_and_prunes():
    psl = bm.PSL(PSL_TEXT)
    src = {n: {"malware"} for n in ("evil.test", "a.evil.test", "b.evil.test", "x.y.evil.test", "hz.test", "sub.hz2.test",
                                    "gone.test", "a.gone.test", "co.test")}
    plat = {f"s{i}.freehost.test": {"malware"} for i in range(5)} | {"freehost.test": {"ads"}}
    src.update(plat)
    parents, exact, st, rest, reg = bm.merge(src, {"hz.test", "hz2.test"}, psl, {"gone.test"}, platform_min=5)
    assert st["hagezi_covered"] == 2                                     # hz.test itself, sub.hz2.test by parent
    assert "evil.test" in parents and not ({"a.evil.test", "b.evil.test", "x.y.evil.test"} & exact)
    assert "freehost.test" in exact and {f"s{i}.freehost.test" for i in range(5)} <= exact   # platform: never folded
    assert st["pruned_dead"] == 2 and not ({"gone.test", "a.gone.test"} & (exact | parents))
    assert "co.test" not in exact and st["bare_suffix_dropped"] == 1
    assert st["merged_entries"] == len(parents) + len(exact) and "gone.test" in rest   # pruned, still archived


def test_state_machine_needs_two_nxdomains_a_week_apart_and_revives(tmp_path):
    c = conf(tmp_path)
    db = bm.results_db(tmp_path / "r.sqlite", create=True)
    assert bm.observe(db, "gone.test", "NXDOMAIN", NOW, c) == "nx-pending"
    assert bm.observe(db, "gone.test", "NXDOMAIN", NOW + 2 * DAY, c) == "nx-pending"      # too soon
    assert bm.observe(db, "gone.test", "SERVFAIL", NOW + 5 * DAY, c) == "nx-pending"      # no evidence either way
    assert bm.observe(db, "gone.test", "NXDOMAIN", NOW + 8 * DAY, c) == "dead"
    assert bm.observe(db, "gone.test", "NOERROR", NOW + 40 * DAY, c) == "revived"
    assert bm.observe(db, "gone.test", "NXDOMAIN", NOW + 41 * DAY, c) == "nx-pending"     # the clock starts again
    tr = [tuple(r) for r in db.execute("SELECT from_state, to_state FROM transitions ORDER BY ts")]
    assert tr == [("unchecked", "nx-pending"), ("nx-pending", "dead"), ("dead", "revived"), ("revived", "nx-pending")]


def test_due_order_priority_unchecked_then_due_confirmations(tmp_path):
    c = conf(tmp_path)
    db = bm.results_db(tmp_path / "r.sqlite", create=True)
    db.executemany("INSERT INTO reg (name) VALUES (?)", [("new.test",), ("seen.test",)])
    bm.observe(db, "pend.test", "NXDOMAIN", NOW - 8 * DAY, c)
    bm.observe(db, "fresh.test", "NXDOMAIN", NOW - DAY, c)
    bm.observe(db, "ok.test", "NOERROR", NOW - DAY, c)
    db.execute("UPDATE reg SET priority = 1 WHERE name = 'seen.test'")
    assert bm.due(db, 10, NOW, c)[:3] == ["seen.test", "new.test", "pend.test"]
    assert "fresh.test" not in bm.due(db, 10, NOW, c)                                  # its confirmation is not due


def write_queue(c, rows):
    p = Path(c["CHECK_DIR"]); p.mkdir(parents=True, exist_ok=True)
    with gzip.open(p / "queue.txt.gz", "wt") as fh:
        fh.write("# q\n" + "".join(f"{n}\t{pr}\n" for n, pr in rows))


def test_check_imports_the_queue_skips_when_metered_or_loaded_and_records(tmp_path):
    c = conf(tmp_path, METERED_IFACES="enu1")
    write_queue(c, [("gone.test", 0), ("alive.test", 0), ("seen.test", 1)])
    calls = []
    fake = lambda names, qps: calls.append(list(names)) or {n: ("NXDOMAIN" if n == "gone.test" else "NOERROR") for n in names}
    assert bm.check(c, now=NOW, resolver=fake, iface="enu1")["skipped"] == "metered" and calls == []
    assert bm.check(c, now=NOW, resolver=fake, iface="end0", load=99)["skipped"] == "load" and calls == []
    r = bm.check(c, now=NOW, resolver=fake, iface="end0", load=1, budget=2)
    assert r["checked"] == 2 and calls[0][0] == "seen.test"                             # priority first
    bm.check(c, now=NOW + 60, resolver=fake, iface="end0", load=1, budget=5)
    db = bm.results_db(Path(c["CHECK_DIR"]) / "results.sqlite")
    st = dict(db.execute("SELECT name, state FROM reg"))
    assert st == {"gone.test": "nx-pending", "alive.test": "alive", "seen.test": "alive"}


SRC = {"https://blocklistproject.github.io/Lists/malware.txt": "0.0.0.0 evil.test\n0.0.0.0 a.evil.test\n0.0.0.0 gone.test\n0.0.0.0 hz.test\n0.0.0.0 lone.example\n",
       "https://blocklistproject.github.io/Lists/ads.txt": "0.0.0.0 ads.example\n",
       "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/tif.txt": "*.hz.test\n",
       "https://publicsuffix.org/list/public_suffix_list.dat": PSL_TEXT}


def fetcher(table):
    def f(u):
        if u in table:
            return table[u].encode()
        raise OSError("offline")
    return f


def test_build_writes_both_lists_archives_queues_and_runs_gravity_once(tmp_path, monkeypatch):
    c = conf(tmp_path)
    runs = []
    assert bm.build(c, fetch=fetcher(SRC), now=NOW, iface="end0", gravity=lambda: runs.append(1) or 0, load=1) == 0
    hosts = (tmp_path / "lists" / bm.HOSTS_FILE).read_text()
    abp = (tmp_path / "lists" / bm.ABP_FILE).read_text()
    assert abp.startswith("[Adblock Plus]\n") and "||evil.test^" in abp and "a.evil.test" not in hosts
    assert {"gone.test", "lone.example", "ads.example"} <= set(hosts.split()) and "hz.test" not in hosts
    assert runs == [1]
    assert bm.build(c, fetch=fetcher(SRC), now=NOW + 60, iface="end0", gravity=lambda: runs.append(1) or 0, load=1) == 0
    assert runs == [1]                                                                  # unchanged lists: no gravity
    j = json.loads((tmp_path / "out/latest.json").read_text())
    assert j["merged_entries"] == 4 and j["hagezi_covered"] == 1 and j["abp_parents"] == 1
    arch = sorted(p.name for p in (tmp_path / "out/archive").iterdir())
    assert arch == ["2026-11-snapshot.tsv.gz"]                                        # first build: snapshot only
    with gzip.open(tmp_path / "check/queue.txt.gz", "rt") as fh:
        assert {l.split("\t")[0] for l in fh if not l.startswith("#")} == {"evil.test", "gone.test", "lone.example", "ads.example"}
    assert oct((tmp_path / "lists" / bm.HOSTS_FILE).stat().st_mode & 0o777) == "0o644"


def test_build_prunes_dead_and_a_lookup_of_a_pruned_domain_jumps_the_queue(tmp_path, monkeypatch):
    c = conf(tmp_path)
    db = bm.results_db(Path(c["CHECK_DIR"]) / "results.sqlite", create=True)
    bm.observe(db, "gone.test", "NXDOMAIN", NOW - 9 * DAY, c); bm.observe(db, "gone.test", "NXDOMAIN", NOW - DAY, c); db.commit()
    (tmp_path / "pihole.log").write_text("Nov 18 x dnsmasq[1]: query[A] www.gone.test from 192.0.2.10\n")
    sent = []
    monkeypatch.setattr(bm, "push", lambda c_, t, m, p=3: sent.append(t))
    bm.build(c, fetch=fetcher(SRC), now=NOW, iface="end0", gravity=lambda: 0, load=1)
    assert "gone.test" not in (tmp_path / "lists" / bm.HOSTS_FILE).read_text().split()
    with gzip.open(tmp_path / "check/queue.txt.gz", "rt") as fh:
        assert "gone.test\t1\n" in fh.read()
    assert sent == ["blocklist-merge: 1 pruned domain(s) looked up here"]
    assert json.loads((tmp_path / "out/latest.json").read_text())["pruned_dead"] == 1


def test_build_refuses_a_broken_fetch_and_skips_on_metered(tmp_path):
    c = conf(tmp_path, MIN_ENTRIES="100", METERED_IFACES="enu1")
    assert bm.build(c, fetch=fetcher(SRC), now=NOW, iface="end0", gravity=lambda: 0, load=1) == 1
    assert not (tmp_path / "lists" / bm.HOSTS_FILE).exists()
    assert bm.build(c, fetch=lambda u: (_ for _ in ()).throw(AssertionError("no fetch")), iface="enu1") == 0
    c2 = conf(tmp_path)
    assert bm.build(c2, fetch=fetcher(SRC), now=NOW, iface="end0", gravity=lambda: 0, load=50) == 0
    assert "pending" in json.loads((tmp_path / "out/latest.json").read_text())["gravity"]


def test_selftest_positive_blocked_negative_nxdomain(tmp_path, monkeypatch):
    c = conf(tmp_path)
    bm.build(c, fetch=fetcher(SRC), now=NOW, iface="end0", gravity=lambda: 0, load=1)
    db = bm.results_db(Path(c["CHECK_DIR"]) / "results.sqlite", create=True)
    bm.observe(db, "lone.example", "NOERROR", NOW, c)
    bm.observe(db, "gone.test", "NXDOMAIN", NOW - 9 * DAY, c); bm.observe(db, "gone.test", "NXDOMAIN", NOW, c); db.commit()
    monkeypatch.setattr(bm, "push", lambda *a, **k: None)
    good = lambda server, name: ("NOERROR", ["0.0.0.0"]) if server.endswith("#53") else ("NXDOMAIN", [])
    assert bm.selftest(c, query=good, seed=1) == 0
    leak = lambda server, name: ("NOERROR", ["198.51.100.9"]) if server.endswith("#53") else ("NXDOMAIN", [])
    assert bm.selftest(c, query=leak, seed=1) == 1


def test_contrib_drafts_dead_entries_and_hagezi_candidates(tmp_path):
    c = conf(tmp_path)
    bm.build(c, fetch=fetcher(SRC), now=NOW, iface="end0", gravity=lambda: 0, load=1)
    db = bm.results_db(Path(c["CHECK_DIR"]) / "results.sqlite", create=True)
    bm.observe(db, "gone.test", "NXDOMAIN", NOW - 9 * DAY, c); bm.observe(db, "gone.test", "NXDOMAIN", NOW, c)
    bm.observe(db, "lone.example", "NOERROR", NOW, c); db.commit()
    (tmp_path / "pihole.log").write_text("x query[A] lone.example from 192.0.2.10\n")
    od = bm.contrib(c, now=NOW)
    dead = (od / "blp-dead-2026-11-18.csv").read_text().splitlines()
    cand = (od / "hagezi-candidates-2026-11-18.csv").read_text().splitlines()
    assert dead[0].startswith("name,") and dead[1].startswith("gone.test,malware,gone.test,")
    assert cand[1].startswith("lone.example,malware,lone.example,")


def test_units_and_installer_rules():
    chk = (D / "ctdc-blmerge-check.service").read_text()
    assert "User=unbound" in chk and "ReadWritePaths=/var/lib/ctdc-blmerge-check" in chk and "ProtectSystem=strict" in chk
    b = (D / "ctdc-blmerge-build.service").read_text()
    assert "build --conf" in b and "selftest --conf" in b and "Nice=19" in b
    a = (D / "apply.sh").read_text()
    assert 'python3 "$HERE/' not in a and "(( EUID == 0 ))" in a
    assert "revert" in a[a.index("switch() {"):a.index("revert() {")]            # a switch that loads nothing undoes itself
    assert "OnCalendar=hourly" in (D / "ctdc-blmerge-check.timer").read_text()
    src = (D / "blocklist_merge.py").read_text()
    assert "urlopen" not in src[src.index("def check("):src.index("def seen_pruned(")]  # the checker never fetches lists


def test_no_real_list_content_in_tracked_files():
    names = re.compile(r"\b[a-z0-9-]+\.(?:com|net|org|ru|cn|xyz|top)\b")
    allowed = {"blocklistproject.github.io", "publicsuffix.org", "raw.githubusercontent.com", "github.com"}
    for p in (REPO / "tests/scripts/test_blocklist_merge.py",):
        found = {m.group(0) for m in names.finditer(p.read_text())}
        assert not (found - {a.split(".", 1)[-1] if a.count(".") > 1 else a for a in allowed} - {"github.com"}), found
