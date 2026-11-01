"""Vault addressing must distinguish host loopback from container ingress."""
import importlib.util
from pathlib import Path
import pytest

@pytest.mark.parametrize('marker,expected', [
    (None, 'http://127.0.0.1:8090/remote.php/dav/files'),
    ('/run/.containerenv', 'http://100.x.x.x:80/remote.php/dav/files'),
    ('/.dockerenv', 'http://100.x.x.x:80/remote.php/dav/files'),
])
def test_vault_address_matches_process_network(monkeypatch, marker, expected):
    monkeypatch.setenv('NEXTCLOUD_ADMIN_USER', 'fixture')
    monkeypatch.delenv('NEXTCLOUD_WEBDAV_BASE', raising=False)
    import os
    actual_exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path:
        path == marker if path in ('/run/.containerenv', '/.dockerenv') else actual_exists(path))
    spec = importlib.util.spec_from_file_location('fixture_webdav',
        Path(__file__).resolve().parents[2] / 'src/second_brain/webdav_client.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.WEBDAV_BASE == expected
    monkeypatch.setenv('NEXTCLOUD_WEBDAV_BASE', 'http://fixture.invalid/dav')
    assert module._default_webdav_base() == 'http://fixture.invalid/dav'
