"""Regression tests for the 2026-08-16 drift-audit vault traversal guard.

The old inline check (`".." in path or path.startswith("/")`) was defeated
by double-URL-encoding: `%252e%252e` survives Starlette's single decode as
`%2e%2e`, then requests' requote_uri decodes it again to `..` before it
reaches the WebDAV backend. _vault_path_is_safe fully decodes and re-checks.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import pytest

from web.main import _vault_path_is_safe


@pytest.mark.parametrize("path", [
    "01-Sources/personal-notes/Series/uber/note.md",
    "04-Syntheses/",
    "01-Sources/personal-notes/Series",
    "foo/bar%20baz.md",   # legit %20 space -> decodes to a normal name
])
def test_legitimate_paths_allowed(path):
    assert _vault_path_is_safe(path) is True


@pytest.mark.parametrize("path", [
    "",                       # empty
    "/etc/passwd",            # absolute
    "../etc/passwd",          # plain traversal
    "%2e%2e/secret",          # single-encoded
    "%252e%252e/secret",      # double-encoded (the reported gap)
    "%25252e%25252e/x",       # triple-encoded
    "a/..%2f..%2fb",          # mixed encoded slash traversal
    "a\\..\\b",               # backslash traversal
])
def test_traversal_and_encoded_variants_rejected(path):
    assert _vault_path_is_safe(path) is False
