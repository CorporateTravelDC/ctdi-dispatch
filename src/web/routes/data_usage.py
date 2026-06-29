"""web.routes.data_usage — per-feed data usage endpoint."""
from __future__ import annotations
from fastapi import APIRouter
from common import db

router = APIRouter()


@router.get("/api/v1/feeds/usage")
def get_data_usage():
    """
    Per-feed data usage statistics since last restart.
    Shows raw bytes received from each source (pre-filter) and how many
    records passed client-side filtering vs were dropped.
    """
    rows = db.get_feed_data_usage()
    total_bytes = sum(r["bytes_in"] for r in rows)
    total_accepted = sum(r["records_accepted"] for r in rows)
    total_in = sum(r["records_in"] for r in rows)
    return {
        "summary": {
            "total_bytes_in": total_bytes,
            "total_bytes_in_mb": round(total_bytes / 1_048_576, 2),
            "total_records_in": total_in,
            "total_records_accepted": total_accepted,
            "total_records_dropped": max(0, total_in - total_accepted),
        },
        "feeds": rows,
    }
