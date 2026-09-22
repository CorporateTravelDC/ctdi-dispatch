"""
Regression tests for the 2026-08-25 C-9 fix (Opus blind review):
require_admin() used to raise its 403 before ever reaching db.audit(), so
a failed authorization attempt left zero trace, and the audit row for a
successful call stored the raw request body verbatim (vault notes, sudo
commands, feed URLs that commonly embed an API key) for 90 days. This
locks in: denied attempts are now audited too, and sensitive-looking
values are redacted before being persisted.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from starlette.requests import Request
from fastapi.security import HTTPAuthorizationCredentials

from auth import auth
from auth.auth import Tier, _redact_audit_detail, require_admin


def _make_request(method: str = "GET", body: bytes = b"") -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": method,
        "path": "/admin/whatever",
        "headers": [],
        "query_string": b"",
        "client": ("10.x.x.x", 12345),
    }
    return Request(scope, receive=receive)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_denied_authorization_is_audited_before_raising():
    dep = require_admin("admin.test.action")
    request = _make_request()
    with patch.object(auth.db, "lookup_token", return_value=None), \
         patch.object(auth.db, "audit") as mock_audit:
        with pytest.raises(Exception):
            asyncio.run(dep(request, Tier.T0, None))
        mock_audit.assert_called_once()
        args = mock_audit.call_args[0]
        assert args[0] == "admin.test.action"
        assert args[1] == Tier.T0.value
        assert args[4] == {"result": "denied"}


def test_successful_call_redacts_sensitive_body_fields():
    dep = require_admin("admin.test.action")
    body = b'{"notes": "real vault note text", "count": 3}'
    request = _make_request(method="POST", body=body)
    with patch.object(auth.db, "lookup_token", return_value={"token_prefix": "ctdc_admin_"}), \
         patch.object(auth.db, "audit") as mock_audit:
        result = asyncio.run(dep(request, Tier.ADMIN, _creds("ctdc_admin_x")))
    assert result == Tier.ADMIN
    mock_audit.assert_called_once()
    detail = mock_audit.call_args[0][4]
    assert detail["count"] == 3
    assert detail["notes"] != "real vault note text"
    assert detail["notes"].startswith("<redacted:")


def test_redact_audit_detail_strips_feed_url_api_key():
    detail = {"feed_url": "https://example.com/feed?api_key=supersecretvalue&format=json"}
    redacted = _redact_audit_detail(detail)
    assert "supersecretvalue" not in redacted["feed_url"]
    assert "format=json" in redacted["feed_url"]


def test_redact_audit_detail_strips_userinfo():
    detail = {"url": "https://user:hunter2@example.com/path"}
    redacted = _redact_audit_detail(detail)
    assert "hunter2" not in redacted["url"]
    assert "user" not in redacted["url"]


def test_redact_audit_detail_leaves_ordinary_fields_alone():
    detail = {"sector": "DCA-N", "enabled": True}
    assert _redact_audit_detail(detail) == detail
