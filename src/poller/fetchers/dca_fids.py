"""poller/fetchers/dca_fids.py -- FETCH_SCHEDULE entry for DCA FIDS."""
from poller.fetchers.airport_fids import run_for

def run() -> dict:
    return run_for("DCA")
