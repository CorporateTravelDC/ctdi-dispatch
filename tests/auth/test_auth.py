"""Unit tests for the tiered-auth resolver in auth.auth.resolve_tier.

Covers the target model: elevation above Tier 0 requires BOTH a
non-public-origin request AND a valid bearer token. The X-CTDI-Public
marker (set only by nginx on tunnel-fronted vhosts) forces Tier 0 before
any token lookup runs, regardless of what else rides along on the
request (including a forged Tailscale-range X-Forwarded-For).
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from starlette.requests import Request
from fastapi.security import HTTPAuthorizationCredentials

from auth import auth
from auth.auth import Tier


def _make_request(headers: dict) -> Request:
    encoded = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "headers": encoded,
        "method": "GET",
        "path": "/",
    }
    return Request(scope)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_public_marker_with_admin_token_resolves_t0():
    request = _make_request({"X-CTDI-Public": "1"})
    with patch.object(auth.db, "lookup_token", return_value={"tier": "admin"}):
        assert auth.resolve_tier(request, _creds("ctdc_x_admintoken")) == Tier.T0


def test_no_marker_admin_token_resolves_admin():
    request = _make_request({})
    with patch.object(auth.db, "lookup_token", return_value={"tier": "admin"}):
        assert auth.resolve_tier(request, _creds("ctdc_x_admintoken")) == Tier.ADMIN


def test_no_marker_cert_token_resolves_t1():
    request = _make_request({})
    with patch.object(auth.db, "lookup_token", return_value={"tier": "cert"}):
        assert auth.resolve_tier(request, _creds("ctdc_x_certtoken")) == Tier.T1


def test_no_marker_no_token_resolves_t0():
    request = _make_request({})
    with patch.object(auth.db, "lookup_token", return_value=None):
        assert auth.resolve_tier(request, None) == Tier.T0


def test_marker_no_token_resolves_t0():
    request = _make_request({"X-CTDI-Public": "1"})
    with patch.object(auth.db, "lookup_token", return_value=None):
        assert auth.resolve_tier(request, None) == Tier.T0


def test_forged_tailscale_xff_through_marked_vhost_still_t0():
    """A forged X-Forwarded-For in the Tailscale CGNAT range must not
    matter -- only the nginx-authoritative X-CTDI-Public marker is
    consulted. Tailnet identity headers no longer grant any tier."""
    request = _make_request({
        "X-CTDI-Public": "1",
        "X-Forwarded-For": "100.64.0.1",
        "Tailscale-User-Login": "someone@example.ts.net",
    })
    with patch.object(auth.db, "lookup_token", return_value={"tier": "admin"}):
        assert auth.resolve_tier(request, _creds("ctdc_x_admintoken")) == Tier.T0
