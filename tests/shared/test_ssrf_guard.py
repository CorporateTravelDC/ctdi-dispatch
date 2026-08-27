"""
Regression tests for the 2026-08-26 C-13 fix (Opus blind review):
shared.ssrf_guard.is_safe_public_url() -- used by runner/main.py's
rss_custom() and poller.osint_monitor's _fetch_feed() to reject a
caller-supplied/admin-configured URL that resolves to a private/internal
address before ever making the outbound request.

Also covers the same-day follow-up fix: plain socket.getaddrinfo() has no
timeout of its own, so a hostname whose DNS resolution hangs blocked this
function indefinitely -- confirmed live as `Skill osint-monitor timed out
after 2000s` firing on every single poller cycle, since this guard runs
BEFORE osint_monitor.py's own FETCH_TIMEOUT=20s httpx call ever starts.
"""
import socket
import time
from unittest.mock import patch

from shared.ssrf_guard import DNS_TIMEOUT_SECS, is_safe_public_url


def test_rejects_loopback():
    ok, reason = is_safe_public_url("http://127.0.0.1:8001/admin/version")
    assert ok is False
    assert "private" in reason or "internal" in reason


def test_rejects_link_local_metadata_ip():
    ok, _ = is_safe_public_url("http://169.254.169.254/latest/meta-data/")
    assert ok is False


def test_rejects_rfc1918_private_range():
    ok, _ = is_safe_public_url("http://10.x.x.x/")
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


def test_hung_dns_resolution_times_out_instead_of_blocking_forever():
    """The exact live bug: a feed whose DNS resolution never returns must
    not block this function for longer than DNS_TIMEOUT_SECS -- it used
    to block forever (no timeout at all), which starved the calling
    skill's own per-fetch timeout of ever running."""
    def _hang(*_args, **_kwargs):
        time.sleep(DNS_TIMEOUT_SECS + 30)
        return []  # never actually reached within the test

    with patch("socket.getaddrinfo", side_effect=_hang):
        started = time.monotonic()
        ok, reason = is_safe_public_url("http://slow-dns-example.test/feed")
        elapsed = time.monotonic() - started

    assert ok is False
    assert "did not complete" in reason
    assert elapsed < DNS_TIMEOUT_SECS + 5, (
        f"is_safe_public_url() took {elapsed:.1f}s -- the DNS timeout "
        "guard did not bound the call as expected"
    )
