"""
tests/conftest.py -- global, unconditional safety net for the WHOLE test
suite. Applies to every test in this repo, no opt-in required.

CRITICAL CONTEXT (2026-09-05 real incident): this repo has no separate
dev/staging box -- the Pi this runs on IS the live production dispatch
system, and every config default in common/config.py points at REAL
production infrastructure: DISPATCH_DB's default IS the real
corporatetraveldc.db path, and NTFY_URL's default (http://localhost:8080)
IS the real ntfy server, reachable from a bare host process since the
ntfy container publishes that port to the host. There is no environment
variable that needs to be SET for a test to hit production -- the
DANGEROUS state is the default, unset one, which is exactly what a
freshly-opened shell has.

Confirmed live: running the test suite directly on this box sent REAL
ntfy push notifications to the operator's phone -- fake test flights
(UAL123, AAL5265) appeared as real-looking flight alerts, because
neither a new test file nor several pre-existing ones (added_by='test'
rows had been sitting in the live watchlist_entries table since
2026-08-27/08-30, from some prior session) isolated db._db_path or the
ntfy send path, and the "obviously safe-looking" defaults were not
actually safe on this specific box. Real, live traffic sharing those
same flight numbers matched the stale test watchlist rows and fired for
real. The stale rows were deleted from production the same day this was
found; this conftest is the fix so it can't happen again.

These fixtures are intentionally unconditional (autouse=True, no marker
needed) and apply BEFORE any test module's own setup runs. A test that
wants to verify real send()/DB-path behavior mocks it itself as before
(e.g. tests/common/test_ntfy_push_*.py already does
`patch("common.ntfy_push.requests.post", ...)`) -- that mock simply
overrides these defaults for the scope of its own `with`/fixture, exactly
as it did before this file existed; none of the existing test suite was
ever relying on reaching real production infrastructure.
"""
import pytest


@pytest.fixture(autouse=True)
def _block_real_ntfy_sends(monkeypatch):
    """Hard block on the actual HTTP call ntfy_push.send() makes --
    regardless of what NTFY_URL/NTFY_FALLBACK_URL/NTFY_TOKEN resolve to,
    no test can ever reach a real ntfy server through this call site."""
    import common.ntfy_push as ntfy_push

    class _SafeFakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            pass

    def _safe_post(*args, **kwargs):
        return _SafeFakeResponse()

    monkeypatch.setattr(ntfy_push.requests, "post", _safe_post)


@pytest.fixture(autouse=True)
def _isolate_default_db_path(monkeypatch, tmp_path):
    """Every test gets an isolated DB file by default. db._db_path() never
    resolves to the real corporatetraveldc.db unless a test explicitly
    re-monkeypatches it -- no test in this suite should ever need to."""
    import common.db as db
    fallback_db = tmp_path / "conftest-default.db"
    monkeypatch.setattr(db, "_db_path", lambda: fallback_db)


@pytest.fixture(autouse=True)
def _isolate_state_dir(monkeypatch, tmp_path):
    """common.config.state_dir() (DISPATCH_STATE_DIR) backs several
    real-file writers besides the DB -- common.push_dedup.PushDedup's
    pusher-{name}-dedup.json files among them (confirmed live: a fake
    "watch-entry-1"/"watch-entry-2" test key ended up written into the
    real production pusher-tbfm_watchlist-dedup.json during this same
    incident). Redirected here so any such writer defaults to a tmp dir
    instead of /var/lib/corporatetraveldc."""
    import common.config as config
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "state_dir", lambda: str(state_dir))
