"""
Regression test for the 2026-08-25 C-30 fix (Opus blind review):
scrub-public-tree.py treated .docx/.pptx as opaque blobs and ran the same
byte-level SUBSTITUTIONS/REGEX_SWEEPS pass used for text files -- which
can never match anything, since the original plaintext isn't a findable
substring inside a DEFLATE-compressed XML stream. Confirmed live: 17
public investor .docx/.pptx files carried the operator's real name/domain
in their compressed XML while the scrub pipeline reported them clean.
This locks in the corrected contract: OOXML files are decompressed,
scrubbed part-by-part, and repackaged as a valid zip.
"""
import importlib.util
import io
import sys
import zipfile
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "scrub-public-tree.py"
_spec = importlib.util.spec_from_file_location("scrub_public_tree", _SCRIPT_PATH)
scrub_public_tree = importlib.util.module_from_spec(_spec)
sys.modules["scrub_public_tree"] = scrub_public_tree
_spec.loader.exec_module(scrub_public_tree)


def _make_docx(document_xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_scrub_ooxml_bytes_redacts_domain_inside_compressed_xml():
    original = _make_docx(
        b"<w:t>Contact: someone@example.com for details</w:t>"
    )
    scrubbed = scrub_public_tree._scrub_ooxml_bytes(original)

    zf = zipfile.ZipFile(io.BytesIO(scrubbed))
    doc_xml = zf.read("word/document.xml")
    assert b"example.com" not in doc_xml
    assert b"example.com" in doc_xml


def test_scrub_ooxml_bytes_produces_a_valid_openable_zip():
    original = _make_docx(b"<w:t>plain content, nothing sensitive</w:t>")
    scrubbed = scrub_public_tree._scrub_ooxml_bytes(original)

    zf = zipfile.ZipFile(io.BytesIO(scrubbed))
    assert zf.testzip() is None
    assert zf.read("word/document.xml") == b"<w:t>plain content, nothing sensitive</w:t>"


def test_scrub_ooxml_bytes_falls_back_on_invalid_zip():
    not_a_zip = b"this is not actually a zip file"
    result = scrub_public_tree._scrub_ooxml_bytes(not_a_zip)
    assert result == not_a_zip  # no substitutions match, but no crash either


def test_ooxml_text_parts_returns_decompressed_xml_for_verify_scrubbed():
    original = _make_docx(b"<w:t>the operator was here</w:t>")
    parts = scrub_public_tree._ooxml_text_parts(original)
    assert any(b"the operator was here" in p for p in parts), (
        "verify_scrubbed's independent re-check must be able to see "
        "inside the compressed XML, not just the outer zip bytes"
    )


def test_ooxml_text_parts_empty_for_invalid_zip():
    assert scrub_public_tree._ooxml_text_parts(b"not a zip") == []
