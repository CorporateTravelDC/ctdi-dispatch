"""2026-11-12: vault paths are percent-encoded. A raw '#' in a note name made
the rest of the path a URL fragment, so the PUT hit the parent folder (409)."""
import importlib.util
import types
from pathlib import Path


def load(monkeypatch):
    monkeypatch.setenv('NEXTCLOUD_ADMIN_USER', 'fixture')
    monkeypatch.setenv('NEXTCLOUD_WEBDAV_BASE', 'http://fixture.invalid/dav')
    spec = importlib.util.spec_from_file_location('fixture_webdav_enc',
        Path(__file__).resolve().parents[2] / 'src/second_brain/webdav_client.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    calls = []
    def fake(method, url, **kw):
        calls.append((method, url))
        return types.SimpleNamespace(status_code=201, raise_for_status=lambda: None)
    monkeypatch.setattr(m.requests, 'request', fake)
    monkeypatch.setattr(m, '_auth', lambda: ('fixture', 'fixture'))
    return m, calls


def test_hash_and_question_mark_names_reach_the_file_not_the_folder(monkeypatch):
    m, calls = load(monkeypatch)
    m.put('01-Sources/personal-notes/#Explainer: what? 100%.md', 'x')
    method, url = calls[-1]
    assert method == 'PUT'
    assert url == ('http://fixture.invalid/dav/fixture/01-Sources/personal-notes/'
                   '%23Explainer%3A%20what%3F%20100%25.md')
    assert '#' not in url and '?' not in url


def test_folders_and_plain_names_are_unchanged(monkeypatch):
    m, calls = load(monkeypatch)
    m.put('01-Sources/personal-notes/plain-note.md', 'x')
    assert ('MKCOL', 'http://fixture.invalid/dav/fixture/01-Sources') in calls
    assert calls[-1][1] == 'http://fixture.invalid/dav/fixture/01-Sources/personal-notes/plain-note.md'
