"""
Regression test for the 2026-08-26 C-19 fix (Opus blind review):
check_fdps_watchlist() used to also check `gufi != entry.get(
"gufi_override", "")` as an alternate match path -- but "gufi_override"
is never set on any watchlist entry anywhere in the codebase. With the
key always missing (default ""), an FDPS message whose GUFI parsed empty
made that OR-arm's negation False for every entry, so it matched -- and
fired a watchlist hit for -- every active entry regardless of callsign.
This locks in the corrected contract: only callsign matching is used, and
an empty-GUFI message never mass-matches unrelated watchlist entries.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from ingest.parsers.fdps_parser import check_fdps_watchlist


def _entry(identifier):
    return {"id": "entry-1", "identifier": identifier, "hex_id": "abc123"}


def test_empty_gufi_does_not_false_match_unrelated_entry():
    """The exact C-19 regression: an unrelated flight (callsign doesn't
    match) with an empty/unparsed GUFI must NOT fire a watchlist hit."""
    parsed = {"source": "FH", "callsign": "UAL123", "gufi": ""}
    with patch("shared.watchlist.get_active_entries", return_value=[_entry("DAL456")]), \
         patch("shared.watchlist.watchlist_event_hit") as mock_hit:
        check_fdps_watchlist(parsed)
    mock_hit.assert_not_called()


def test_matching_callsign_still_fires_normally():
    parsed = {"source": "FH", "callsign": "DAL456", "gufi": "", "origin": "KDCA", "destination": "KORD"}
    with patch("shared.watchlist.get_active_entries", return_value=[_entry("DAL456")]), \
         patch("shared.watchlist.watchlist_event_hit") as mock_hit, \
         patch("ingest.parsers.fdps_parser._fire_fdps_nas_alert"):
        check_fdps_watchlist(parsed)
    mock_hit.assert_called_once()


def test_populated_gufi_still_does_not_false_match_unrelated_entry():
    parsed = {"source": "FH", "callsign": "UAL123", "gufi": "real-gufi-value-123"}
    with patch("shared.watchlist.get_active_entries", return_value=[_entry("DAL456")]), \
         patch("shared.watchlist.watchlist_event_hit") as mock_hit:
        check_fdps_watchlist(parsed)
    mock_hit.assert_not_called()
