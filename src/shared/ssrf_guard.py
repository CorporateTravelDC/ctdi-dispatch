"""
shared.ssrf_guard -- host/IP validation for server-side fetches of
caller-supplied or admin-configured URLs.

2026-08-26 (Opus blind review C-13): extracted from runner/main.py's
rss_custom() fix so poller.osint_monitor's admin-configured feed_urls
fetch path can use the same check -- both are "this process makes an
outbound HTTP request to a URL that ultimately traces back to something
other than a hardcoded, reviewed source," the class of bug SSRF is.

2026-08-26 follow-up (found live, same day): plain socket.getaddrinfo()
has NO timeout of its own -- a single feed whose hostname's DNS
resolution hangs (dead resolver, black-holed query, slow authoritative
server) blocked here indefinitely, with nothing downstream to catch it.
This defeated osint_monitor.py's own FETCH_TIMEOUT=20s on the httpx.get()
that comes AFTER this check, since the hang happened before the fetch
ever started. Confirmed live: `Skill osint-monitor timed out after 2000s`
fired on every single poller cycle since this guard was added -- the
skill-level watchdog was the only thing ever stopping it. Wrapped in a
bounded thread with a short deadline; a DNS resolution that can't
complete in DNS_TIMEOUT_SECS is treated as unsafe (fail closed) rather
than hung forever.
"""
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError
from urllib.parse import urlsplit

DNS_TIMEOUT_SECS = 5


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Resolve the URL's hostname and reject anything that lands in a
    private/loopback/link-local/reserved/multicast range. Returns
    (is_safe, reason_if_not). Does not itself fetch the URL."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        return False, "URL has no host"
    # NOT a context manager on purpose: `with ThreadPoolExecutor()` calls
    # shutdown(wait=True) on exit, which would block on the very same
    # hung getaddrinfo() call we're trying to time out on -- reintroducing
    # the exact indefinite hang this fix exists to prevent. shutdown(wait=
    # False) lets the orphaned resolver thread finish (or not) on its own
    # in the background without this function ever waiting on it again.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(socket.getaddrinfo, host, None)
        addrs = {info[4][0] for info in future.result(timeout=DNS_TIMEOUT_SECS)}
    except socket.gaierror as e:
        return False, f"could not resolve host: {e}"
    except _FutureTimeoutError:
        return False, f"DNS resolution did not complete within {DNS_TIMEOUT_SECS}s"
    finally:
        pool.shutdown(wait=False)
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False, f"unresolvable address {addr!r}"
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, "URL resolves to a private/internal address"
    return True, ""
