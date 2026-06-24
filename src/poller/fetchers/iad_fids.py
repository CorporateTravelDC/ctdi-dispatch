"""poller/fetchers/iad_fids.py -- FETCH_SCHEDULE entry for IAD FIDS."""
from poller.fetchers.airport_fids import run_for

def run() -> dict:
    return run_for("IAD")
