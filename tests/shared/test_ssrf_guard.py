"""
Regression tests for the 2026-08-26 C-13 fix (Opus blind review):
shared.ssrf_guard.is_safe_public_url() -- used by runner/main.py's
rss_custom() and poller.osint_monitor's _fetch_feed() to reject a
caller-supplied/admin-configured URL that resolves to a private/internal
address before ever making the outbound request.
"""
from shared.ssrf_guard import is_safe_public_url


def test_rejects_loopback():
    ok, reason = is_safe_public_url("http://127.0.0.1:8001/admin/version")
    assert ok is False
    assert "private" in reason or "internal" in reason


def test_rejects_link_local_metadata_ip():
    ok, _ = is_safe_public_url("http://169.254.169.254/latest/meta-data/")
    assert ok is False


def test_rejects_rfc1918_private_range():
    ok, _ = is_safe_public_url("http://10.0.0.5/")
    assert ok is False


def test_rejects_unresolvable_host():
    ok, reason = is_safe_public_url("http://this-host-does-not-exist.invalid/")
    assert ok is False
    assert "resolve" in reason


def test_allows_real_public_dns_resolver_ip():
    # 1.1.1.1 has no reverse-DNS trickery risk and is a stable, always-public
    # anchor for a "does the allow path work at all" check.
    ok, reason = is_safe_public_url("http://1.1.1.1/")
    assert ok is True, reason


def test_rejects_url_with_no_host():
    ok, reason = is_safe_public_url("not-a-url")
    assert ok is False
    assert "no host" in reason
