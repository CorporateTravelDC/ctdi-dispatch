"""tests/runner/test_osm_tile_cache.py -- same-origin OSM tile cache (2026-11-03): referrer-trimming
browsers got OSM's blocked tile; the runner now fetches and caches tiles itself. No network: the
upstream client is faked."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from fastapi.testclient import TestClient  # noqa: E402

from runner import main as rm  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"tile" * 10


class _Upstream:
    def __init__(self, status=200, ctype="image/png", cc="max-age=95832", fail=False):
        self.calls = []
        self.status, self.ctype, self.cc, self.fail = status, ctype, cc, fail

    def client(self, *a, **k):
        outer = self

        class _C:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *e):
                return False

            async def get(self, url, headers=None):
                outer.calls.append((url, dict(headers or {})))
                if outer.fail:
                    raise httpx.ConnectError("down")
                return httpx.Response(outer.status, content=PNG,
                                      headers={"content-type": outer.ctype, "cache-control": outer.cc})
        return _C()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "_TILE_CACHE_DIR", str(tmp_path / "tiles"))
    monkeypatch.setattr(rm, "_is_trusted", lambda request: True)
    return TestClient(rm.app)


def test_miss_fetches_with_identifying_agent_and_no_referer_then_hits_cache(client, monkeypatch):
    up = _Upstream()
    monkeypatch.setattr(rm.httpx, "AsyncClient", up.client)
    r1 = client.get("/tiles/osm/10/292/391.png")
    r2 = client.get("/tiles/osm/10/292/391.png")
    assert r1.status_code == r2.status_code == 200 and r1.content == r2.content == PNG
    assert len(up.calls) == 1                                    # second request served from the cache
    url, headers = up.calls[0]
    assert url == "https://tile.openstreetmap.org/10/292/391.png"
    assert "developer@csexecutiveservices.com" in headers["User-Agent"]
    assert "Referer" not in headers


@pytest.mark.parametrize("path", ["/tiles/osm/20/0/0.png", "/tiles/osm/3/8/0.png", "/tiles/osm/3/0/8.png",
                                  "/tiles/osm/-1/0/0.png"])
def test_out_of_range_coordinates_never_reach_upstream(client, monkeypatch, path):
    up = _Upstream()
    monkeypatch.setattr(rm.httpx, "AsyncClient", up.client)
    assert client.get(path).status_code in (404, 422)
    assert up.calls == []


def test_upstream_error_serves_a_stale_tile_but_never_a_non_png(client, monkeypatch):
    up = _Upstream()
    monkeypatch.setattr(rm.httpx, "AsyncClient", up.client)
    assert client.get("/tiles/osm/5/1/1.png").status_code == 200
    meta = Path(rm._tile_path(5, 1, 1) + ".exp")
    meta.write_text(str(time.time() - 10))                       # expired, still inside the stale window
    up.fail = True
    r = client.get("/tiles/osm/5/1/1.png")
    assert r.status_code == 200 and r.content == PNG
    up2 = _Upstream(ctype="text/html")
    monkeypatch.setattr(rm.httpx, "AsyncClient", up2.client)
    assert client.get("/tiles/osm/6/1/1.png").status_code == 502


def test_frontend_uses_the_same_origin_tiles():
    js = (Path(rm.__file__).parent / "frontend/src/mapTiles.js").read_text()
    assert "export const OSM_TILE_URL = '/tiles/osm/{z}/{x}/{y}.png'" in js
