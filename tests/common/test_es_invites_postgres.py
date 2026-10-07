"""
tests/common/test_es_invites_postgres.py -- the Executive Standard invite flow
(common/es_invites.py) against the SCRATCH Postgres database with migration
0072 applied: Postgres types (boolean source flag, DOUBLE PRECISION times,
rowcount-guarded single use) behave like the sqlite twin. Skips if the scratch
database is unreachable; refuses to run against any other database name.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from common import config, db_backend  # noqa: E402
from common import es_invites as es  # noqa: E402

SCRATCH_DB = "corporatetraveldc_pgphase1_test"


def _reachable() -> bool:
    try:
        import psycopg
        conninfo = db_backend._pg_conninfo().replace(  # noqa: SLF001
            f"dbname={config.get('DISPATCH_PG_DB', 'corporatetraveldc')}", f"dbname={SCRATCH_DB}")
        with psycopg.connect(conninfo, connect_timeout=3) as c:
            return c.execute("SELECT to_regclass('public.es_grants')").fetchone()[0] is not None
    except Exception:
        return False


pytestmark = [pytest.mark.postgres_scratch, pytest.mark.skipif(
    not _reachable(), reason=f"scratch database {SCRATCH_DB!r} unreachable or migration 0072 not applied")]


@pytest.fixture(autouse=True)
def pg(monkeypatch):
    monkeypatch.setenv("DISPATCH_PG_DB", SCRATCH_DB)
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    assert config.get("DISPATCH_PG_DB", "corporatetraveldc") == SCRATCH_DB
    db_backend.close_pool()
    with db_backend.pg_conn() as c:
        for t in ("es_grants", "es_promos", "es_exchanges", "es_sessions", "es_invite_settings", "es_invite_events"):
            c.execute(f"DELETE FROM {t}")
        c.execute("UPDATE exec_standard_sources SET enabled = true WHERE source IN ('direct', 'promo')")
    yield
    db_backend.close_pool()


def _x(url):
    return parse_qs(urlparse(url).query)["x"][0]


def test_invite_promo_revoke_kill_on_postgres():
    r = es.invite_issue("op", "pg@example.com")
    code = r["url"].rsplit("/i/", 1)[1]
    sid, gid = es.exchange(_x(es.claim_invite(code)))
    assert es.session_check(sid) == gid == r["grant"]
    with pytest.raises(es.InviteError):
        es.exchange(_x(es.claim_invite(code)) + "x")
    p = es.promo_create("op", "pg booth", uses=1)
    psid, _ = es.exchange(_x(es.redeem_promo(p["code"])))
    with pytest.raises(es.InviteError):
        es.redeem_promo(p["code"])
    with db_backend.pg_conn() as c:
        c.execute("UPDATE exec_standard_sources SET enabled = false WHERE source = 'promo'")
    assert es.session_check(psid) is None
    assert es.revoke_grant("op", "pg@example.com")["devices_signed_out"] == 1
    assert es.session_check(sid) is None
    es.kill_all("op")
    assert es.frozen() and es.summary()["frozen"]
    assert es.events()[0]["action"] == "kill-all"
