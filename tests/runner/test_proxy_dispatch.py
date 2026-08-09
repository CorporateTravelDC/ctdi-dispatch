"""Unit tests for the runner's dispatch-proxy auth headers and admin gate.

_dispatch_proxy_headers is a pure helper extracted from proxy_dispatch()
specifically so this logic is testable without async test infrastructure.
It must uphold two invariants of the target auth model:
  1. A client-supplied token is forwarded as-is, and the X-CTDI-Public
     marker rides along with it -- a token arriving through the public
     tunnel still can't elevate dispatch's tier.
  2. The RUNNER_ENRICHED_TOKEN service-injection branch (used for the
     whitelisted Tier-1 GET paths) never forwards the marker, so it keeps
     resolving to Tier 1 unconditionally.

_is_trusted guards the runner's own /admin routes and must key off the
same nginx-authoritative marker rather than request.client.host (which is
always nginx's own loopback peer address in this proxied deployment).
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from starlette.requests import Request

from runner import main as runner_main


def _make_request(headers: dict) -> Request:
    encoded = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "headers": encoded,
        "method": "GET",
        "path": "/api/dispatch/api/v1/tfr-enriched",
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_client_token_with_public_marker_forwards_marker():
    request = _make_request({
        "Authorization": "Bearer ctdc_x_sometoken",
        "X-CTDI-Public": "1",
    })
    headers = runner_main._dispatch_proxy_headers(request, "api/v1/tfr-enriched")
    assert headers == {
        "Authorization": "Bearer ctdc_x_sometoken",
        "X-CTDI-Public": "1",
    }


def test_client_token_without_marker_omits_marker():
    request = _make_request({"Authorization": "Bearer ctdc_x_sometoken"})
    headers = runner_main._dispatch_proxy_headers(request, "api/v1/tfr-enriched")
    assert headers == {"Authorization": "Bearer ctdc_x_sometoken"}


def test_enriched_token_injection_never_forwards_marker_even_if_public():
    """Even when the inbound request is marked public, the server-side
    service-token injection branch must not carry the marker downstream --
    it has to keep resolving to Tier 1 regardless of viewer origin."""
    request = _make_request({"X-CTDI-Public": "1"})
    with patch.object(runner_main, "RUNNER_ENRICHED_TOKEN", "ctdc_svc_enrichedtoken"):
        headers = runner_main._dispatch_proxy_headers(request, "api/v1/tfr-enriched")
    assert headers == {"Authorization": "Bearer ctdc_svc_enrichedtoken"}
    assert "X-CTDI-Public" not in headers


def test_no_token_and_path_not_whitelisted_yields_empty_headers():
    request = _make_request({})
    with patch.object(runner_main, "RUNNER_ENRICHED_TOKEN", "ctdc_svc_enrichedtoken"):
        headers = runner_main._dispatch_proxy_headers(request, "api/v1/some-other-path")
    assert headers == {}


def test_is_trusted_false_when_public_marker_present():
    request = _make_request({"X-CTDI-Public": "1"})
    assert runner_main._is_trusted(request) is False


def test_is_trusted_true_when_marker_absent():
    request = _make_request({})
    assert runner_main._is_trusted(request) is True
