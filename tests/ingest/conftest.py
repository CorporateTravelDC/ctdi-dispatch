"""
tests/ingest/conftest.py -- added 2026-08-30 late-night pass.

WHY THIS EXISTS: every ingest parser carries a self-limited debug-capture
writer (first N messages of the PROCESS go to
/var/lib/corporatetraveldc/*_debug*). In production those directories
hold real captured SWIM traffic -- the ground truth this test suite's
real-data tests read. But the capture counters are per-process and start
at zero in the pytest process too, so any test that parses a message
through a parser entry point silently OVERWRITES the live capture files
with whatever that test happened to parse. This was not hypothetical:
a 2026-08-30 full-suite run overwrote fdps_debug_fixm30/sample_4.xml
and sample_6.xml -- the only captured copies of a real HU/AH agreed-route
re-expression pair (JIA5230) -- with recycled test traffic, minutes after
they were captured. (The route strings and identity fields survive as a
repo fixture snapshot; the original envelopes do not.) Newer test files
had each grown their own per-test monkeypatching for this; the older
files never did, and one unguarded parse is all it takes.

The fix is central and unconditional: every capture DIRECTORY constant is
pointed into a session-scoped temp dir before any test runs. Tests that
exercise the capture mechanics themselves keep working -- they monkeypatch
their own tmp_path dir per-test on top of this, and capture behavior is
unchanged, just never aimed at the live directories.
"""
import pytest


# (module path, attribute) for every live-directory capture constant in
# the ingest tree. getattr-guarded below so a renamed/removed constant
# fails loudly in one place instead of silently re-opening the hole.
_CAPTURE_DIR_ATTRS = [
    ("ingest.parsers.fdps_parser", "_DEBUG_SAMPLE_DIR"),
    ("ingest.parsers.fdps_parser", "_DEBUG_SAMPLE_DIR_FIXM30"),
    ("ingest.parsers.tfms_parser", "_DEBUG_SAMPLE_DIR"),
    ("ingest.parsers.tfms_parser", "_UNKNOWN_MSGTYPE_DIR"),
    ("ingest.parsers.tbfm_parser", "_DEBUG_SAMPLE_DIR"),
    ("ingest.parsers.itws_parser", "_DEBUG_SAMPLE_DIR"),
    ("ingest.parsers.smes_parser", "_DEBUG_SAMPLE_DIR"),
    ("ingest.swim_client", "_BAD_MSG_CAPTURE_DIR"),
]

# Secondary capture dirs (verified present 2026-08-30; probed with the
# same hard assert as the primary list -- silence is the failure mode
# this file exists to prevent).
_OPTIONAL_CAPTURE_DIR_ATTRS = [
    ("ingest.parsers.tbfm_parser", "_UNKNOWN_KIND_DIR"),
    ("ingest.parsers.smes_parser", "_PRIORITY_SAMPLE_DIR"),
    ("ingest.parsers.itws_parser", "_PRODUCT_SAMPLE_DIR"),
    ("ingest.parsers.itws_parser", "_PARSE_FAILURE_DIR"),
]


@pytest.fixture(scope="session", autouse=True)
def _redirect_live_capture_dirs(tmp_path_factory):
    import importlib
    base = tmp_path_factory.mktemp("capture_quarantine")
    for i, (mod_name, attr) in enumerate(_CAPTURE_DIR_ATTRS):
        mod = importlib.import_module(mod_name)
        assert hasattr(mod, attr), (
            f"{mod_name}.{attr} is gone -- update conftest so the live "
            f"capture directories stay protected from test writes")
        setattr(mod, attr, str(base / f"{mod_name.split('.')[-1]}_{attr}_{i}"))
    for i, (mod_name, attr) in enumerate(_OPTIONAL_CAPTURE_DIR_ATTRS):
        mod = importlib.import_module(mod_name)
        assert hasattr(mod, attr), (
            f"{mod_name}.{attr} is gone -- update conftest so the live "
            f"capture directories stay protected from test writes")
        setattr(mod, attr, str(base / f"opt_{attr}_{i}"))
    yield
