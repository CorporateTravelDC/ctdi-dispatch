"""
shared.ssrf_guard -- host/IP validation for server-side fetches of
caller-supplied or admin-configured URLs.

2026-08-26 (Opus blind review C-13): extracted from runner/main.py's
rss_custom() fix so poller.osint_monitor's admin-configured feed_urls
fetch path can use the same check -- both are "this process makes an
outbound HTTP request to a URL that ultimately traces back to something
other than a hardcoded, reviewed source," the class of bug SSRF is.
"""
import ipaddress
import socket
from urllib.parse import urlsplit


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Resolve the URL's hostname and reject anything that lands in a
    private/loopback/link-local/reserved/multicast range. Returns
    (is_safe, reason_if_not). Does not itself fetch the URL."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        return False, "URL has no host"
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as e:
        return False, f"could not resolve host: {e}"
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False, f"unresolvable address {addr!r}"
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, "URL resolves to a private/internal address"
    return True, ""
