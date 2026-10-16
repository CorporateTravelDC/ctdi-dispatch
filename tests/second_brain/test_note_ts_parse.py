"""Parity audit 2026-10-15: note_ts is stored as the WebDAV mtime (RFC 1123) or indexed_at (ISO);
the causal/cluster window parsed it with fromisoformat() only, skipped every note, and both tiers
stayed at 0 edges. _parse_note_ts() reads both forms."""
from datetime import timezone

from second_brain.semantic.compile import _parse_note_ts


def test_rfc1123_mtime():
    dt = _parse_note_ts("Wed, 15 Oct 2026 17:58:00 GMT")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 10, 15, 17, 58) and dt.tzinfo is not None


def test_iso_and_naive_iso():
    assert _parse_note_ts("2026-10-15T17:58:00+00:00").hour == 17
    assert _parse_note_ts("2026-10-15T17:58:00").tzinfo == timezone.utc


def test_garbage_is_none():
    assert _parse_note_ts("") is None and _parse_note_ts(None) is None and _parse_note_ts("not a date") is None
