"""
poller/fetchers/airport_fids.py
--------------------------------
Feed-state heartbeat fetcher for DCA and IAD FIDS.

Registered in FETCH_SCHEDULE (poller/main.py) as two entries:
  {"name": "dca_fids", "module": "poller.fetchers.airport_fids", "interval": 60}
  {"name": "iad_fids", "module": "poller.fetchers.airport_fids", "interval": 60}

The poller dispatcher calls mod.run() but cannot pass arguments, so we
read the target airport from the module-level AIRPORT variable that the
scheduler sets via a thin per-airport wrapper approach.

Because the dispatcher uses importlib.import_module(module_name) and calls
mod.run(), and both DCA and IAD share this module, the scheduler entries
use distinct wrapper modules (see dca_fids.py and iad_fids.py) that set
AIRPORT before delegating here.
"""

import hashlib
import logging
import time
from typing import Optional

from common import db
from common.airport_fids import get_data, get_payload_hash

log = logging.getLogger(__name__)


def run_for(airport: str) -> dict:
    """
    Fetch FIDS for the given airport and stamp feed_state.
    Called by the per-airport wrapper modules (dca_fids.py / iad_fids.py).
    """
    feed_name = f"{airport.lower()}_fids"
    fetched_at = time.time()

    data = get_data(airport, force=True)

    if data is None:
        msg = f"FIDS fetch failed for {airport}"
        log.warning("airport_fids.run_for(%s): %s", airport, msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}

    arrivals = len(data.get("arrivals", []))
    departures = len(data.get("departures", []))
    payload_hash = get_payload_hash(airport)

    db.upsert_feed(feed_name, fetched_at, error=None, payload_hash=payload_hash)
    log.info("airport_fids %s OK -- %d arrivals, %d departures", airport, arrivals, departures)
    return {"airport": airport, "arrivals": arrivals, "departures": departures}
