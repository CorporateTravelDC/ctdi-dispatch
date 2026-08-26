"""
Regression test for the 2026-08-25 C-14 fix (Opus blind review):
expire_nws_alerts() is only ever called by the REST poller with
REST-sourced alert_ids, but its old unscoped DELETE removed every
push-sourced (`nwws:*`) row too on every REST poll -- confirmed live, 22
real push rows were wiped on the first REST poll after a push-to-REST
failover. This locks in the corrected contract: only REST-sourced rows
are ever removed by this function.
"""
import tempfile
from pathlib import Path

import common.db as db


def test_expire_nws_alerts_never_touches_push_sourced_rows():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig_db_path = db._db_path
    try:
        db._db_path = lambda: Path(tmp.name)
        db.init_db_all()

        # A push-sourced alert (ingest/nwws.py's alert_id convention).
        db.upsert_nws_alert(
            alert_id="nwws:KLWX:TO.W:1787700000",
            event_type="Tornado Warning", area_desc="District of Columbia",
            severity="Extreme", certainty="Observed",
            effective=0, expires=9999999999,
            headline="push-sourced", description="",
        )
        # A REST-sourced alert that will go stale on the next REST poll.
        db.upsert_nws_alert(
            alert_id="urn:oid:REST-alert-840-stale",
            event_type="Wind Advisory", area_desc="District of Columbia",
            severity="Moderate", certainty="Likely",
            effective=0, expires=9999999999,
            headline="rest-sourced-stale", description="",
        )
        # A REST-sourced alert that's still active on the next poll.
        db.upsert_nws_alert(
            alert_id="urn:oid:REST-alert-840-fresh",
            event_type="Wind Advisory", area_desc="District of Columbia",
            severity="Moderate", certainty="Likely",
            effective=0, expires=9999999999,
            headline="rest-sourced-fresh", description="",
        )

        # Simulate the REST poller's next cycle: only the still-active
        # REST id is in active_ids -- the stale REST row should be
        # removed, but the push-sourced row must survive untouched.
        db.expire_nws_alerts(["urn:oid:REST-alert-840-fresh"])

        remaining = {a["alert_id"] for a in db.get_active_nws_alerts()}
        assert "nwws:KLWX:TO.W:1787700000" in remaining, (
            "push-sourced row was wiped by the REST-only expiry sweep -- "
            "this is the exact C-14 regression"
        )
        assert "urn:oid:REST-alert-840-fresh" in remaining
        assert "urn:oid:REST-alert-840-stale" not in remaining
    finally:
        db._db_path = orig_db_path
        Path(tmp.name).unlink(missing_ok=True)
