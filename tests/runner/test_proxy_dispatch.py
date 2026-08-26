"""Unit tests for the runner's dispatch-proxy auth headers and trust check.

Rewritten 2026-08-20 against VERIFIED LIVE BEHAVIOR, not assumed design --
the previous version of this file asserted an X-CTDI-Public-propagation
model that was never actually implemented, and _dispatch_proxy_headers had
been inlined into proxy_dispatch() at some point without anyone updating
or removing this file, so all 5 tests here had been silently broken
(AttributeError) with nothing catching it. See the second-brain note from
2026-08-20 for the full investigation trail (live curl against the public
hostname, ~/.cloudflared/config.yml's own comments, tailscale lock status).

Verified facts this file now tests against, not assumes:
  1. _dispatch_proxy_headers never forwards X-CTDI-Public to dispatch-web,
     regardless of whether the inbound request carried it. Only
     Authorization is ever set. This is fine for this deployment: the
     runner's public demo hostname (dispatch-runner.example.com)
     has no Cloudflare Access policy and Tailnet Lock doesn't apply to
     this traffic path either -- confirmed live, not assumed -- its only
     intended gate is proxy_dispatch()'s own DEMO_MODE + session-cookie
     check, unrelated to this header or to _is_trusted().
  2. _is_trusted() is pure IP-based (CF-Connecting-IP if present, else
     request.client.host/X-Forwarded-For against _TRUSTED_NETS). It does
     NOT check X-CTDI-Public and never has -- there is no code path in
     this file that references that header at all.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from starlette.requests import Request

from runner import main as runner_main


def _make_request(headers: dict, client: tuple = ("127.0.0.1", 12345)) -> Request:
    encoded = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "headers": encoded,
        "method": "GET",
        "path": "/api/dispatch/api/v1/tfr-enriched",
        "client": client,
    }
    return Request(scope)


# ── _dispatch_proxy_headers ─────────────────────────────────────────────────

def test_client_token_forwarded_as_is_marker_never_carried():
    request = _make_request({
        "Authorization": "Bearer ctdc_x_sometoken",
        "X-CTDI-Public": "1",
    })
    headers = runner_main._dispatch_proxy_headers(request, "api/v1/tfr-enriched")
    # X-CTDI-Public is never forwarded, present on the inbound request or not.
    assert headers == {"Authorization": "Bearer ctdc_x_sometoken"}


def test_client_token_without_marker_forwarded_as_is():
    request = _make_request({"Authorization": "Bearer ctdc_x_sometoken"})
    headers = runner_main._dispatch_proxy_headers(request, "api/v1/tfr-enriched")
    assert headers == {"Authorization": "Bearer ctdc_x_sometoken"}


def test_enriched_token_injection_for_tier1_path_ignores_public_marker():
    """The server-side injection branch for _TIER1_PATHS is unconditional
    on path membership alone -- it doesn't check _is_trusted() or
    X-CTDI-Public, by design (these paths are intentionally widened to the
    public Ops view). Confirms that stays true regardless of the inbound
    marker, and that the marker still never rides along on the outbound
    request either way."""
    request = _make_request({"X-CTDI-Public": "1"})
    with patch.object(runner_main, "RUNNER_ENRICHED_TOKEN", "ctdc_svc_enrichedtoken"), \
         patch.object(runner_main, "_TIER1_PATHS", frozenset({"api/v1/tfr-enriched"})):
        headers = runner_main._dispatch_proxy_headers(request, "api/v1/tfr-enriched")
    assert headers == {"Authorization": "Bearer ctdc_svc_enrichedtoken"}
    assert "X-CTDI-Public" not in headers


def test_no_token_and_path_not_whitelisted_yields_empty_headers():
    request = _make_request({})
    with patch.object(runner_main, "RUNNER_ENRICHED_TOKEN", "ctdc_svc_enrichedtoken"), \
         patch.object(runner_main, "_TIER1_PATHS", frozenset()), \
         patch.object(runner_main, "_TIER1_PATHS_TRUSTED_ORIGIN_ONLY", frozenset()):
        headers = runner_main._dispatch_proxy_headers(request, "api/v1/some-other-path")
    assert headers == {}


# ── _is_trusted ──────────────────────────────────────────────────────────────
# No test here asserts X-CTDI-Public affects the result -- verified live
# 2026-08-20 that it doesn't. What actually matters, tested below: the
# CF-Connecting-IP branch (real Cloudflare tunnel traffic) and the
# loopback/private-net fallback (real Tailscale/LAN traffic, and this
# deployment's actual proxied-through-nginx pattern where
# request.client.host is always 127.0.0.1).

def test_is_trusted_true_for_loopback_client_no_cf_header():
    """The real pattern for every request this runner receives when
    proxied through nginx: request.client.host is nginx's own loopback
    hop (127.0.0.1), which is inside _TRUSTED_NETS. No CF-Connecting-IP
    present -- e.g. genuine Tailscale/LAN-origin traffic, or any request
    to a vhost (like dispatch-runner's) that doesn't sit behind Cloudflare
    Access. Verified live 2026-08-20 this is real, current behavior, not
    a hypothetical."""
    request = _make_request({})
    assert runner_main._is_trusted(request) is True


def test_is_trusted_true_for_real_tailnet_cf_connecting_ip():
    """CF-Connecting-IP present, Host is the actual Cloudflare-fronted
    hostname, and the IP is within the Tailscale CGNAT range (100.64.0.0/10,
    in _TRUSTED_NETS) -- e.g. a request that legitimately traversed
    Cloudflare from a tailnet-adjacent origin."""
    request = _make_request({
        "CF-Connecting-IP": "100.x.x.x",
        "Host": "dispatch-runner.example.com",
    })
    assert runner_main._is_trusted(request) is True


def test_is_trusted_false_for_real_external_cf_connecting_ip():
    """CF-Connecting-IP present, Host is the real Cloudflare-fronted
    hostname, and the IP is NOT in any _TRUSTED_NETS range -- a genuine
    public-internet caller reaching this box through the Cloudflare
    tunnel. This is the property that actually protects anything gated on
    _is_trusted() (e.g. _TIER1_PATHS_TRUSTED_ORIGIN_ONLY injection, or a
    DEMO_MODE-enabled vhost's password gate) -- confirmed it correctly
    evaluates the real external IP rather than falling through to the
    trusted loopback hop, per the 2026-07-21 fix in this function's own
    docstring."""
    request = _make_request({
        "CF-Connecting-IP": "8.8.8.8",
        "Host": "dispatch-runner.example.com",
    })
    assert runner_main._is_trusted(request) is False


# ── C-2 regression: CF-Connecting-IP must only be honored on the one
# hostname actually fronted by Cloudflare (2026-08-25 Opus blind review) ──

def test_is_trusted_ignores_cf_header_on_tailnet_hostname():
    """The exact live exploit: a caller reaching the tailnet-only vhost
    directly (no Cloudflare anywhere in that path) sets CF-Connecting-IP
    to a trusted-looking value itself. Confirmed live pre-fix this
    returned True; must now fall through to the direct-IP check instead
    of trusting a self-reported header on a host Cloudflare never fronts."""
    request = _make_request(
        {
            "CF-Connecting-IP": "100.64.1.1",
            "Host": "corporatetraveldc-dispatch.tailxxxxxxx.ts.net",
        },
        client=("8.8.8.8", 12345),
    )
    assert runner_main._is_trusted(request) is False


def test_is_trusted_ignores_cf_header_with_no_host_at_all():
    """Same exploit shape with no Host header sent at all (e.g. a raw IP
    request) -- must not default-trust CF-Connecting-IP just because it's
    present."""
    request = _make_request(
        {"CF-Connecting-IP": "100.64.1.1"},
        client=("8.8.8.8", 12345),
    )
    assert runner_main._is_trusted(request) is False


def test_is_trusted_falls_through_correctly_when_cf_header_ignored():
    """When CF-Connecting-IP is ignored (wrong host) but the ACTUAL peer
    is genuinely trusted, the direct-IP fallback still correctly grants
    trust -- the fix narrows which header is honored, it doesn't break
    the legitimate loopback/tailnet fallback path."""
    request = _make_request(
        {
            "CF-Connecting-IP": "8.8.8.8",  # untrusted, and on the wrong host anyway
            "Host": "corporatetraveldc-dispatch.tailxxxxxxx.ts.net",
        },
        client=("127.0.0.1", 12345),
    )
    assert runner_main._is_trusted(request) is True
