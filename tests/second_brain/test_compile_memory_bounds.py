"""Bound memory without dropping geometric matches or weakening transactions."""
import sqlite3
import tracemalloc
import pytest
from second_brain.semantic import compile as sc


class Cursor:
    def __init__(self,owner,name=None):
        self.owner=owner;self.name=name;self.closed=False;self.itersize=None;self.writing=False
    def execute(self,sql,params=()):
        self.owner.reads.append((self.name,sql,params))
        return self
    def __iter__(self):
        yield from self.owner.notes()
    def fetchall(self):
        return list(self.owner.notes())
    def executemany(self,sql,rows,**kwargs):
        self.writing=True
        assert not kwargs.get("returning",False), "batch must not retain per-row results"
        n=len(rows);self.owner.max_batch=max(self.owner.max_batch,n)
        if self.owner.fail:raise RuntimeError("synthetic write failure")
        self.owner.writes["refs" if "instance_refs" in sql else "edges"]+=n
    def close(self):self.closed=True


class Connection:
    def __init__(self,n=0,fail=False):
        self.n=n;self.fail=fail;self.cursors=[];self.reads=[]
        self.writes={"refs":0,"edges":0};self.max_batch=0
    def notes(self):
        for i in range(self.n):
            yield {"path":f"synthetic/{i}.md","mtime":"2026-10-01T00:00:00+00:00",
                   "indexed_at":"2026-10-01T00:00:00+00:00","title":"",
                   "content":f"UAS ID TST{i:05d}"}
    def cursor(self,name=None):
        c=Cursor(self,name);self.cursors.append(c);return c
    def execute(self,sql,params=()):return self.cursor().execute(sql,params)
    def executemany(self,sql,rows,**kwargs):return self.cursor().executemany(sql,rows,**kwargs)
    def commit(self):raise AssertionError("a compile phase must not commit independently")


def test_dense_geometry_preserves_every_match_with_bounded_memory(monkeypatch,record_property):
    monkeypatch.setattr(sc,"_valid_flight_pairs",lambda c:set())
    monkeypatch.setattr(sc,"_known_station_codes",lambda c:{})
    monkeypatch.setattr(sc,"_lookup_drone_position",lambda *a:None)
    c=Connection(300)
    tracemalloc.start()
    try:
        count=sc.assign_geometry(c)
        _,peak=tracemalloc.get_traced_memory()
    finally:tracemalloc.stop()
    # All notes share a timestamp and name different instances, so every
    # unordered pair must produce both directed edges, despite batching.
    assert count==300*299==c.writes["edges"]
    assert c.writes["refs"]==300
    record_property("peak_python_bytes",peak)
    record_property("directed_edges",count)
    assert peak < 12*1024*1024, f"peak Python allocation {peak}"
    assert c.max_batch<=1000
    reads=[name for name,sql,_ in c.reads if "FROM vault_documents" in sql]
    assert len(reads)==1 and reads[0] is not None
    assert all(c.closed for c in c.cursors if c.name is not None or c.writing)


def test_batch_failure_propagates_without_commit_and_closes_cursor():
    c=Connection(fail=True)
    with pytest.raises(RuntimeError,match="synthetic write failure"):
        sc._write_batches(c,"INSERT INTO example VALUES (?)",((i,) for i in range(2001)))
    assert c.cursors[0].closed
    assert c.writes=={"refs":0,"edges":0}


def test_stream_is_closed_when_consumer_stops():
    c=Connection(10)
    rows=sc._iter_rows(c,"SELECT synthetic")
    next(rows);rows.close()
    assert c.cursors[0].name is not None and c.cursors[0].closed


def test_stream_and_batches_use_the_real_postgres_translation_wrapper():
    from common.db_backend import _TranslatingConnection

    class RawCursor:
        def __init__(self, name=None):
            self.name = name
            self.closed = False
            self.calls = []
        def execute(self, sql, params):
            self.calls.append((sql, params))
        def __iter__(self):
            yield {'path': 'synthetic/a.md'}
        def executemany(self, sql, rows, **kwargs):
            assert not kwargs.get('returning', False)
            self.calls.append((sql, len(rows)))
        def close(self):
            self.closed = True

    class RawConnection:
        def __init__(self):
            self.cursors = []
        def cursor(self, name=None):
            cur = RawCursor(name)
            self.cursors.append(cur)
            return cur
        def commit(self):
            raise AssertionError('compile phases cannot commit')

    raw = RawConnection()
    conn = _TranslatingConnection(raw)
    assert list(sc._iter_rows(conn, 'SELECT path FROM example WHERE path=?', ('a',))) == [
        {'path': 'synthetic/a.md'}]
    reader = raw.cursors[0]
    assert reader.name.startswith('semantic_compile_')
    assert reader.itersize == 1000 and reader.closed
    assert reader.calls == [('SELECT path FROM example WHERE path=%s', ('a',))]
    count = sc._write_batches(conn, 'INSERT OR IGNORE INTO example VALUES (?)',
                              ((i,) for i in range(2501)))
    assert count == 2501
    writer = raw.cursors[1]
    assert writer.closed and [n for _, n in writer.calls] == [1000, 1000, 501]
    assert all('%s' in sql and 'ON CONFLICT DO NOTHING' in sql for sql, _ in writer.calls)


def test_sqlite_batch_failure_remains_in_callers_transaction():
    c=sqlite3.connect(":memory:")
    c.execute("CREATE TABLE example (n INTEGER PRIMARY KEY)")
    c.execute("INSERT INTO example VALUES (99)");c.commit()
    with pytest.raises(sqlite3.IntegrityError):
        with c:
            c.execute("DELETE FROM example")
            sc._write_batches(c,"INSERT INTO example VALUES (?)",[(i,) for i in range(1500)]+[(10,)])
    assert c.execute("SELECT n FROM example").fetchall()==[(99,)]
    c.close()


def test_geometry_retains_time_only_and_space_only_links_and_excludes_same_instance(monkeypatch):
    monkeypatch.setattr(sc,"_valid_flight_pairs",lambda c:set())
    monkeypatch.setattr(sc,"_known_station_codes",lambda c:{})
    # Synthetic coordinates only; no platform receiver or customer locations.
    positions={"TST00003":(0.0,0.0),"TST00004":(0.01,0.01)}
    monkeypatch.setattr(sc,"_lookup_drone_position",lambda c,ident,epoch:positions.get(ident))
    c=sqlite3.connect(":memory:");c.row_factory=sqlite3.Row
    c.executescript("CREATE TABLE vault_documents (path TEXT,mtime TEXT,indexed_at TEXT);"
                    "CREATE TABLE vault_notes_fulltext (path TEXT,title TEXT,content TEXT);")
    for name,at,ident in [("a","2026-10-01T00:00:00+00:00","TST00001"),
                          ("b","2026-10-01T00:15:00+00:00","TST00002"),
                          ("c","2026-10-01T01:00:00+00:00","TST00003"),
                          ("d","2026-10-02T01:00:00+00:00","TST00004"),
                          ("e","2026-10-01T00:10:00+00:00","TST00001")]:
        c.execute("INSERT INTO vault_documents VALUES (?,?,?)",(name,at,at))
        c.execute("INSERT INTO vault_notes_fulltext VALUES (?, '', ?)",(name,"UAS ID "+ident))
    sc._create_schema(c)
    assert sc.assign_geometry(c)==6
    got={(r["path"],r["target"],r["evidence"].split("matched=")[1]) for r in c.execute(
         "SELECT path,target,evidence FROM semantic_note_derivations WHERE kind='geometric'")}
    assert got=={("a","b","time_only"),("b","a","time_only"),
                 ("b","e","time_only"),("e","b","time_only"),
                 ("c","d","space_only"),("d","c","space_only")}
    c.close()
