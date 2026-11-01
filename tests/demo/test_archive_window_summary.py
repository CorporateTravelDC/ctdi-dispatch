"""Synthetic archive summaries: correct counts, latest payload, bounded decode."""
import importlib
import sqlite3
import tracemalloc
import zlib
from contextlib import contextmanager
import pytest
from demo import promoted_archive as db


@pytest.fixture
def archive(monkeypatch):
    c=sqlite3.connect(":memory:");c.row_factory=sqlite3.Row
    c.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY,endpoint TEXT,captured_at TEXT,payload BLOB,compressed BOOLEAN,synthetic BOOLEAN)")
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)")
    c.execute("CREATE TABLE scrub_ledger (seq INTEGER PRIMARY KEY,run_at TEXT,window_end TEXT,entry_hash TEXT)")
    c.executemany("INSERT INTO meta VALUES (?,?)",[("window_end","2026-10-01T03:00:00"),("promoted_at","2026-10-02T04:00:00Z")])
    c.execute("INSERT INTO scrub_ledger VALUES (1,'2026-10-02T04:00:00Z','2026-10-01T03:00:00',?)",("d"*64,))
    commands=[]
    class Reader:
        def execute(self,sql,params=()):
            commands.append(sql)
            if sql.startswith("SET TRANSACTION"):
                return None
            return c.execute(sql,params)
    @contextmanager
    def demo_conn():yield Reader()
    monkeypatch.setattr(db.db_backend,"demo_pg_conn",demo_conn)
    def forbidden(*args,**kwargs):raise AssertionError("raw production accessor must never be called")
    monkeypatch.setattr(db.db_backend,"pg_conn",forbidden)
    yield c,commands
    c.close()


def test_summary_includes_both_cleared_row_kinds_and_excludes_unpromoted_future(archive):
    c,commands=archive
    c.execute("INSERT INTO snapshots VALUES (1,'weather','2026-09-29',?,0,0)",(b"outside",))
    for i in range(2,22):
        c.execute("INSERT INTO snapshots VALUES (?,'weather','2026-10-01T01:00:00',zeroblob(262144),0,0)",(i,))
    c.execute("INSERT INTO snapshots VALUES (22,'weather','2026-10-01T02:00:00',?,0,0)",(b"latest-cleared",))
    c.execute("INSERT INTO snapshots VALUES (23,'weather','2026-10-01T02:00:00',?,0,1)",(b"synthetic-tie",))
    c.execute("INSERT INTO snapshots VALUES (24,'cps','2026-10-01T03:00:00',?,1,1)",(zlib.compress(b'{"score":0}'),))
    c.execute("INSERT INTO snapshots VALUES (25,'weather','2026-10-02T03:00:00',?,0,0)",(b"beyond-promotion",))
    result=db.fetch_window_summary(30);rows=result.summaries
    assert [x.endpoint for x in rows]==["cps","weather"]
    weather=rows[1]
    assert weather.change_count==22 and weather.synthetic_count==1
    assert weather.first_at=="2026-10-01T01:00:00" and weather.last_at=="2026-10-01T02:00:00"
    assert weather.payload==b"latest-cleared" and not weather.compressed
    assert rows[0].synthetic_count==1
    assert sum(len(x.payload) for x in rows)<100
    assert commands[0]=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    assert all("demo_snapshots" not in query for query in commands)
    assert result.window_end=="2026-10-01T03:00:00+00:00" and result.promotion_seq==1


@pytest.mark.parametrize("broken",["missing-receipt","missing-meta","changed-window","changed-promotion","bad-hash"])
def test_incomplete_promotion_fails_closed_without_raw_fallback(archive,broken):
    c,_=archive
    if broken=="missing-receipt":c.execute("DELETE FROM scrub_ledger")
    elif broken=="missing-meta":c.execute("DELETE FROM meta")
    elif broken=="changed-window":c.execute("UPDATE meta SET value='2026-10-03T00:00:00' WHERE key='window_end'")
    elif broken=="changed-promotion":c.execute("UPDATE meta SET value='2026-10-03T04:00:00Z' WHERE key='promoted_at'")
    else:c.execute("UPDATE scrub_ledger SET entry_hash='not-a-receipt-hash'")
    with pytest.raises(db.PromotionUnavailable):db.fetch_window_summary()


def promoted(rows):
    return db.PromotionWindow("2026-09-29T21:00:00","2026-10-01T03:00:00",
                             "2026-10-02T04:00:00Z",1,"d"*64,rows)


@pytest.fixture
def skill(monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_ADMIN_USER","synthetic-fixture")
    m=importlib.import_module("poller.skills.second_brain_demo_archiver_daily")
    monkeypatch.setattr(m,"log_usage",lambda *a,**k:None)
    return m


@pytest.mark.parametrize("compressed",[True,False])
def test_large_payload_decode_has_bounded_allocation_and_explicit_marker(skill,compressed,record_property):
    payload=b"x"*(5*1024*1024)
    if compressed:payload=zlib.compress(payload)
    tracemalloc.start()
    try:
        out=skill._decode(payload,compressed)
        _,peak=tracemalloc.get_traced_memory()
    finally:tracemalloc.stop()
    record_property("peak_python_bytes",peak)
    assert peak<1024*1024
    assert out.startswith("[truncated before JSON formatting:")
    assert len(out)<66000


def test_section_retains_counts_times_and_latest_json(skill):
    row=db.WindowSummary("weather",200,"first","last",zlib.compress(b'{"condition":"clear"}'),True)
    text=skill._render_endpoint_section(row)
    assert "Changes in window: 200" in text and "first: first, last: last" in text
    assert '"condition": "clear"' in text


def test_corrupt_snapshot_is_labelled_without_exposing_payload(skill):
    row=db.WindowSummary("weather",2,"first","last",b"malformed-compressed-content",True)
    text=skill._render_endpoint_section(row)
    assert "payload decode failed:" in text and "malformed-compressed-content" not in text


def test_blocked_archive_is_failure_and_never_written(skill,monkeypatch):
    monkeypatch.setattr(skill,"_fetch_window",lambda:promoted([db.WindowSummary("weather",1,"first","last",b"{}",False)]))
    def blocked(*a,**k):raise skill.ScrubGateBlocked(["synthetic policy block"])
    monkeypatch.setattr(skill,"gate",blocked)
    def forbidden(*a,**k):raise AssertionError("blocked archive must never be written")
    monkeypatch.setattr(skill.webdav_client,"put",forbidden)
    with pytest.raises(skill.ScrubGateBlocked):skill.main()


def test_vault_archive_uses_summary_counts_and_preserves_isolation(skill,monkeypatch):
    rows=[db.WindowSummary("weather",7,"first","last",b'{"condition":"clear"}',False),
          db.WindowSummary("cps",3,"first","last",b'{"score":0}',False)]
    monkeypatch.setattr(skill,"_fetch_window",lambda:promoted(rows))
    writes=[];indexed=[]
    monkeypatch.setattr(skill.webdav_client,"put",lambda path,note:writes.append((path,note)))
    @contextmanager
    def get_conn():yield object()
    monkeypatch.setattr(skill,"get_conn",get_conn)
    monkeypatch.setattr(skill,"index_note",lambda *a,**k:indexed.append(k))
    skill.main()
    assert len(writes)==len(indexed)==1
    assert "total_changes: 10" in writes[0][1] and "endpoints: 2" in writes[0][1]
    assert "Changes in window: 7" in writes[0][1] and "Changes in window: 3" in writes[0][1]
    assert "data_class: cleared_promoted_demo" in writes[0][1]
    assert "authority: demonstration_only" in writes[0][1]
    assert "raw-feed" not in indexed[0]["tags"]


def test_latest_stored_payload_fetch_is_bounded_before_decode(archive):
    c,_=archive
    c.execute("INSERT INTO snapshots VALUES (1,'weather','2026-10-01T02:00:00',zeroblob(5242880),0,0)")
    tracemalloc.start()
    try:
        result=db.fetch_window_summary()
        _,peak=tracemalloc.get_traced_memory()
    finally:tracemalloc.stop()
    assert len(result.summaries[0].payload)==db.MAX_STORED_PAYLOAD_BYTES
    assert peak<1024*1024


def test_missing_promotion_aborts_before_vault_write(skill,monkeypatch):
    def unavailable():raise db.PromotionUnavailable("fixture")
    monkeypatch.setattr(skill,"_fetch_window",unavailable)
    def forbidden(*a,**k):raise AssertionError("missing promotion must not write")
    monkeypatch.setattr(skill.webdav_client,"put",forbidden)
    with pytest.raises(db.PromotionUnavailable):skill.main()


def test_valid_empty_promoted_window_skips_vault_write(skill,monkeypatch):
    monkeypatch.setattr(skill,"_fetch_window",lambda:promoted([]))
    def forbidden(*a,**k):raise AssertionError("empty promoted window must not write")
    monkeypatch.setattr(skill.webdav_client,"put",forbidden)
    skill.main()
