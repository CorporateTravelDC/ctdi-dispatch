r"""

Regression tests for the ITWS XML sanitizer character-class bug (2026-07-19).

Bug: _sanitize_xml()'s "legal XML 1.0 characters" regex had literal Unicode
characters pasted in where \uXXXX escapes belonged:

    r"[^\x09\x0A\x0D\x20-<U+D7FF literal>-<U+FFFD literal>\U00010000-\U0010FFFF]"

This silently excluded the entire #xE000-#xFFFD range (private-use area plus
most CJK/symbol blocks) from the *allowed* set -- so any legitimate character
in that range in an incoming ITWS message got stripped as if it were an
illegal control character, while a stray literal '-' and U+FFFD were
individually whitelisted as single characters instead of being used as
range bounds. Fixed with explicit \\uXXXX/\\UXXXXXXXX escapes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from ingest.parsers.itws_parser import _sanitize_xml


def test_legal_e000_to_fffd_range_survives_sanitization():
    # A character from the private-use area (U+E100) must NOT be stripped --
    # it is legal per XML 1.0 spec 2.2 (#xE000-#xFFFD).
    raw = "<detail>wxreport</detail>".encode("utf-8")
    out = _sanitize_xml(raw).decode("utf-8")
    assert "" in out, "legal #xE000-#xFFFD character was incorrectly stripped"


def test_illegal_control_characters_are_stripped():
    # C0 control characters outside \t\n\r (e.g. U+0001) are genuinely
    # illegal in XML 1.0 and must still be removed.
    raw = "<detail>wx\x01report</detail>".encode("utf-8")
    out = _sanitize_xml(raw).decode("utf-8")
    assert "\x01" not in out
    assert "wxreport" in out


def test_tab_newline_cr_are_preserved():
    raw = "<detail>line1\nline2\tvalue\rend</detail>".encode("utf-8")
    out = _sanitize_xml(raw).decode("utf-8")
    assert "\n" in out and "\t" in out and "\r" in out


def test_basic_ascii_and_supplementary_plane_survive():
    raw = "<detail>DCA wind shear \U0001F6E9</detail>".encode("utf-8")
    out = _sanitize_xml(raw).decode("utf-8")
    assert "DCA wind shear" in out
    assert "\U0001F6E9" in out


def test_sanitized_output_is_valid_xml():
    import xml.etree.ElementTree as ET
    raw = "<root><detail>wx\x01report</detail></root>".encode("utf-8")
    cleaned = _sanitize_xml(raw)
    root = ET.fromstring(cleaned)  # must not raise
    assert root.find("detail").text == "wxreport"
