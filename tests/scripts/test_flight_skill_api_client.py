"""Personal-token client contracts, without credentials, network or live writes."""
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib import error

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("flight_api", ROOT / "skills/flight-hifi-track/scripts/dispatch_api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.fixture
def personal_file(tmp_path, monkeypatch):
    p = tmp_path / "api-token"
    p.write_text("synthetic" + "-fixture\n")
    p.chmod(0o600)
    monkeypatch.setattr(api, "_token_path", lambda: p)
    return p


def stub_opener(monkeypatch, *, status=200, body=b'{"ok":true}', failure=None):
    calls = []

    class Reply:
        def __enter__(self):
            self.status = status
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return body[:size]

    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            if failure:
                raise failure
            return Reply()

    def build(*handlers):
        assert any(isinstance(h, api._NoRedirect) for h in handlers)
        return Opener()

    monkeypatch.setattr(api.request, "build_opener", build)
    return calls


def test_request_reads_own_file_inside_process_and_confirms_status(personal_file, monkeypatch, capsys):
    calls = stub_opener(monkeypatch, status=201)
    status, body = api.request_json("/api/v1/watchlist/flights", method="POST",
                                  payload={"identifier": "FIX123", "added_by": "fixture"})
    assert status == 201 and body == {"ok": True}
    assert len(calls) == 1
    assert calls[0].get_header("Authorization") == "Bearer synthetic-fixture"
    assert calls[0].full_url == api.BASE_URL + "/api/v1/watchlist/flights"
    assert json.loads(calls[0].data)["added_by"] == "fixture"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_insecure_permissions_stop_before_request(personal_file, monkeypatch, mode):
    personal_file.chmod(mode)
    calls = stub_opener(monkeypatch)
    with pytest.raises(api.DispatchError, match="permissions invalid"):
        api.request_json("/api/v1/watchlist")
    assert calls == []


@pytest.mark.parametrize("state", ["missing", "empty", "symlink", "fifo"])
def test_invalid_file_stops_before_request(personal_file, monkeypatch, tmp_path, state):
    if state == "missing":
        personal_file.unlink()
    elif state == "empty":
        personal_file.write_text("")
    elif state == "symlink":
        other = tmp_path / "fixture-value"
        personal_file.rename(other)
        personal_file.symlink_to(other)
    elif state == "fifo":
        personal_file.unlink()
        os.mkfifo(personal_file, 0o600)
    calls = stub_opener(monkeypatch)
    with pytest.raises(api.DispatchError):
        api.request_json("/api/v1/watchlist")
    assert calls == []


def test_wrong_owner_stops_before_request(personal_file, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(api.os, "fstat", lambda fd: SimpleNamespace(st_mode=0o100600, st_uid=os.getuid() + 1))
        with pytest.raises(api.DispatchError, match="owner/type/permissions"):
            api.personal_token()


def test_public_get_needs_no_personal_token(personal_file, monkeypatch):
    personal_file.unlink()
    calls = stub_opener(monkeypatch)
    assert api.request_json("/api/v1/adsb", auth=False)[0] == 200
    assert calls[0].get_header("Authorization") is None


@pytest.mark.parametrize("route,method,auth,payload", [
    ("https://example.invalid/", "GET", True, None),
    ("//example.invalid/", "GET", True, None),
    ("/api/v1/adsb#fragment", "GET", True, None),
    ("/api/v1/adsb", "GET", True, {}),
    ("/api/v1/adsb", "DELETE", True, None),
    ("/api/v1/watchlist/flights", "POST", False, {}),
])
def test_bad_target_or_request_rejected_before_network(monkeypatch, route, method, auth, payload):
    calls = stub_opener(monkeypatch)
    with pytest.raises(api.DispatchError):
        api.request_json(route, method=method, auth=auth, payload=payload)
    assert calls == []


def test_http_denial_does_not_echo_reflected_body_or_url(personal_file, monkeypatch):
    failure = error.HTTPError("http://example.invalid/reflected-fixture", 403,
                              "reflected-fixture", {}, io.BytesIO(b'reflected-fixture'))
    calls = stub_opener(monkeypatch, failure=failure)
    with pytest.raises(api.DispatchError) as caught:
        api.request_json("/api/v1/watchlist/flights", method="POST", payload={})
    assert str(caught.value) == "Dispatch returned HTTP 403; body withheld"
    assert len(calls) == 1


def test_timeout_is_uncertain_and_not_retried(personal_file, monkeypatch):
    calls = stub_opener(monkeypatch, failure=TimeoutError("reflected-fixture"))
    with pytest.raises(api.DispatchError, match="write outcome may be unknown"):
        api.request_json("/api/v1/watchlist/flights", method="POST", payload={})
    assert len(calls) == 1


def test_redirect_handler_refuses_bearer_forwarding():
    with pytest.raises(api.DispatchError, match="redirect refused"):
        api._NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://example.invalid/")


@pytest.mark.parametrize("body", [b'not-json', b'"unexpected string"'])
def test_invalid_response_body_is_withheld(personal_file, monkeypatch, body):
    stub_opener(monkeypatch, body=body)
    with pytest.raises(api.DispatchError) as caught:
        api.request_json("/api/v1/watchlist")
    assert body.decode() not in str(caught.value)
