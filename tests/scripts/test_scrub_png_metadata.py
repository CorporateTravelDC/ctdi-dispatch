"""tests/scripts/test_scrub_png_metadata.py -- 2026-11-02: PNG metadata chunks
(eXIf, tEXt, zTXt, iTXt, tIME) are stripped from the public mirror; pixel and
colour chunks pass through byte for byte; malformed files are left unchanged."""
from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path

SCRUB = Path(__file__).resolve().parents[2] / "scripts" / "scrub-public-tree.py"


def _load():
    spec = importlib.util.spec_from_file_location("scrub_public_tree_png", SCRUB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


SIG = b"\x89PNG\r\n\x1a\n"
IHDR = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
IDAT = _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
IEND = _chunk(b"IEND", b"")
FAKE_ID = b"11111111-1111-1111-1111-111111111111"


def test_metadata_chunks_are_dropped_and_pixels_kept():
    m = _load()
    png = (SIG + IHDR + _chunk(b"eXIf", b"MM\x00*" + FAKE_ID) + _chunk(b"tEXt", b"Software\x00host")
           + _chunk(b"tIME", b"\x07\xea\x0b\x02\x00\x00\x00") + IDAT + IEND)
    out = m.public_blob_bytes(png, "shot.png", "docs/images/shot.png")
    assert out == SIG + IHDR + IDAT + IEND
    assert FAKE_ID not in out


def test_clean_and_malformed_png_unchanged():
    m = _load()
    clean = SIG + IHDR + IDAT + IEND
    assert m._strip_png_metadata(clean) == clean
    truncated = SIG + IHDR + _chunk(b"eXIf", FAKE_ID)[:-6]
    assert m._strip_png_metadata(truncated) == truncated
    assert m._strip_png_metadata(b"not a png") == b"not a png"


def test_agency_contacts_allowed():
    m = _load()
    for addr in (b"service@navcanada.ca", b"privacy@airservicesaustralia.com", b"aircraftregistrar@caa.govt.nz"):
        assert addr in m.ALLOWED_EMAILS
