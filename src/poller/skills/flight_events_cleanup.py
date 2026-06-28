"""poller.skills.flight_events_cleanup - hourly purge of stale flight_events.

Retention policy:
  - Flights updated within 1h: retained
  - Flights on active watchlist: retained indefinitely
  - Everything else: deleted
"""
from __future__ import annotations
import logging
from common import db

log = logging.getLogger('poller.skills.flight_events_cleanup')


def run() -> None:
    deleted = db.purge_old_flight_events(max_age_seconds=3600)
    if deleted:
        log.info('flight_events_cleanup: purged %d stale row(s)', deleted)
    else:
        log.debug('flight_events_cleanup: nothing to purge')
