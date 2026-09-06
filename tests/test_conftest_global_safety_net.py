"""
Regression test for tests/conftest.py's global, autouse safety net --
see that file's own docstring for the 2026-09-05 real incident this
guards against (a test run on this box sent real ntfy pushes to the
operator's phone using fake test flight data).

Deliberately does NOT do any of its own isolation setup -- the entire
point is proving the autouse fixtures in tests/conftest.py protect a
test that does nothing special, exactly like the ones that caused the
real incident.

_REAL_POST is captured here at module-collection time -- pytest imports
every test module during collection, BEFORE any fixture (autouse
included) runs for any test in the session, so this is genuinely the
original, unpatched `requests.post`, not something already touched by
conftest.py.
"""
import requests as _requests_module

_REAL_POST = _requests_module.post


def test_db_path_is_isolated_by_default():
    import common.db as db
    assert "var/lib/corporatetraveldc" not in str(db._db_path())


def test_state_dir_is_isolated_by_default():
    import common.config as config
    assert "var/lib/corporatetraveldc" not in config.state_dir()


def test_ntfy_post_is_stubbed_by_default():
    import common.ntfy_push as ntfy_push
    assert ntfy_push.requests.post is not _REAL_POST
    resp = ntfy_push.requests.post("http://example.invalid", data=b"x")
    assert resp.status_code == 200
