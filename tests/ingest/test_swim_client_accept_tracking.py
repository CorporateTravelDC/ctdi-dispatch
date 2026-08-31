"""
Regression tests for the accept/reject boolean propagation fix (2026-07-17).

Bug: swim_client.py's receive loop does
    accepted = handler_fn(payload)
    ... 0 if accepted is False else 1
but every _handle_*_message function had no explicit return, so `accepted`
was always None (not False), and feed_data_usage.records_accepted was
reported as 100% for every feed regardless of what the geo/facility filters
inside each parser actually decided. These tests assert each handler now
propagates the real True/False decision instead of implicitly returning None.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import ingest.swim_client as swim_client


# 2026-08-30: the fdps handler consumes the batch-aware
# parse_fdps_messages() (list of dicts) instead of parse_fdps_message()
# (single dict) -- see that function's docstring for the batched-
# MessageCollection drop bug. These three tests mock the new entry point
# with list semantics; the accept contract itself is unchanged.
def test_fdps_handler_returns_false_when_filtered():
    with mock.patch("ingest.parsers.fdps_parser.parse_fdps_messages",
                     return_value=[{"source": "FH", "callsign": "TEST123"}]), \
         mock.patch("ingest.parsers.fdps_parser.write_flight_event", return_value=False), \
         mock.patch("ingest.parsers.fdps_parser.check_marine_one"), \
         mock.patch("ingest.parsers.fdps_parser.check_fdps_watchlist"):
        result = swim_client._handle_fdps_message(b"<xml/>")
    assert result is False, "handler must propagate False, not None, on filtered record"


def test_fdps_handler_returns_true_when_accepted():
    with mock.patch("ingest.parsers.fdps_parser.parse_fdps_messages",
                     return_value=[{"source": "FH", "callsign": "TEST123"}]), \
         mock.patch("ingest.parsers.fdps_parser.write_flight_event", return_value=True), \
         mock.patch("ingest.parsers.fdps_parser.check_marine_one"), \
         mock.patch("ingest.parsers.fdps_parser.check_fdps_watchlist"):
        result = swim_client._handle_fdps_message(b"<xml/>")
    assert result is True


def test_fdps_handler_returns_true_when_any_batched_message_accepted():
    """A batch where only ONE of three flights passes the geo filter must
    still count the payload as accepted -- and every batched flight must
    reach the Marine One + watchlist checks (the pre-2026-08-30 handler
    checked only message[0])."""
    batch = [{"source": "FH", "callsign": f"T{i}"} for i in range(3)]
    marine = mock.MagicMock()
    watch = mock.MagicMock()
    with mock.patch("ingest.parsers.fdps_parser.parse_fdps_messages",
                     return_value=batch), \
         mock.patch("ingest.parsers.fdps_parser.write_flight_event",
                     side_effect=[False, True, False]), \
         mock.patch("ingest.parsers.fdps_parser.check_marine_one", marine), \
         mock.patch("ingest.parsers.fdps_parser.check_fdps_watchlist", watch):
        result = swim_client._handle_fdps_message(b"<xml/>")
    assert result is True
    assert marine.call_count == 3
    assert watch.call_count == 3


def test_fdps_handler_returns_false_on_unparseable_payload():
    with mock.patch("ingest.parsers.fdps_parser.parse_fdps_messages", return_value=[]):
        result = swim_client._handle_fdps_message(b"garbage")
    assert result is False


def test_stdds_handler_returns_false_when_no_tracks_pass():
    with mock.patch("ingest.parsers.smes_parser.parse_smes_message", return_value=[]), \
         mock.patch("ingest.parsers.smes_parser.parse_tais_message", return_value=[]):
        result = swim_client._handle_stdds_message(b"<xml/>")
    assert result is False


def test_stdds_handler_returns_true_when_surface_track_written():
    with mock.patch("ingest.parsers.smes_parser.parse_smes_message",
                     return_value=[{"track_id": "1"}]), \
         mock.patch("ingest.parsers.smes_parser.write_surface_tracks", return_value=1):
        result = swim_client._handle_stdds_message(b"<xml/>")
    assert result is True


def test_tfms_handler_returns_false_when_geo_filtered_to_zero():
    # parser returned candidate programs, but write_tfms_programs geo-filtered
    # all of them out internally (n == 0) -- must surface as False, not None.
    with mock.patch("ingest.parsers.tfms_parser.parse_tfms_message",
                     return_value=[{"facility": "ZLA"}]), \
         mock.patch("ingest.parsers.tfms_parser.write_tfms_programs", return_value=0):
        result = swim_client._handle_tfms_message(b"<xml/>")
    assert result is False


def test_aim_handler_returns_true_when_notam_written():
    with mock.patch("ingest.parsers.aim_parser.parse_aim_message",
                     return_value=[{"facility": "KDCA"}]), \
         mock.patch("ingest.parsers.aim_parser.write_aim_notams", return_value=1):
        result = swim_client._handle_aim_message(b"<xml/>")
    assert result is True


def test_tbfm_handler_returns_false_when_not_zdc():
    with mock.patch("ingest.parsers.tbfm_parser.parse_tbfm_message", return_value=[]):
        result = swim_client._handle_tbfm_message(b"<xml/>")
    assert result is False


def test_itws_handler_returns_true_when_alert_written():
    with mock.patch("ingest.parsers.itws_parser.parse_itws_message",
                     return_value=[{"airport": "KDCA"}]), \
         mock.patch("ingest.parsers.itws_parser.write_itws_alerts", return_value=1), \
         mock.patch("ingest.parsers.itws_parser.check_itws_alerts"):
        result = swim_client._handle_itws_message(b"<xml/>")
    assert result is True


def test_itws_handler_returns_false_when_no_alerts():
    with mock.patch("ingest.parsers.itws_parser.parse_itws_message", return_value=[]):
        result = swim_client._handle_itws_message(b"<xml/>")
    assert result is False
