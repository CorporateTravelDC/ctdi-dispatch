"""Regression test for the 2026-08-16 drift-audit route-shadowing fix.

DELETE /api/v1/watchlist/batch was registered AFTER the dynamic
DELETE /api/v1/watchlist/{entry_id}. Starlette matches in registration
order, so /batch was swallowed by /{entry_id} (entry_id="batch") and the
batch-delete handler was unreachable dead code. This pins the ordering so
the static /batch route always precedes the dynamic catch-all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from web.routes.watchlist import router


def _delete_index(path_suffix: str) -> int:
    for i, r in enumerate(router.routes):
        if getattr(r, "path", "").endswith(path_suffix) and "DELETE" in (r.methods or set()):
            return i
    raise AssertionError(f"no DELETE route ending in {path_suffix!r}")


def test_batch_delete_registered_before_dynamic_entry_id():
    batch_idx = _delete_index("/watchlist/batch")
    dynamic_idx = _delete_index("/watchlist/{entry_id}")
    assert batch_idx < dynamic_idx, (
        "DELETE /batch must be registered before DELETE /{entry_id}, "
        "otherwise the dynamic route shadows it and batch delete 404s."
    )


def test_batch_delete_route_exists_and_is_unique():
    batch_deletes = [
        r for r in router.routes
        if getattr(r, "path", "").endswith("/watchlist/batch")
        and "DELETE" in (r.methods or set())
    ]
    assert len(batch_deletes) == 1
