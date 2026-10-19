"""personal-notes-import downloads only what changed (lean-out L2 / F12, 2026-10-19).

It ran every 2 min and downloaded + hashed EVERY note each time. Now one Depth-0
PROPFIND of the Notes/ root decides whether anything changed (Nextcloud
propagates ETags up the tree); when it did, only notes whose ETag moved are
downloaded. The content hash is still the import decision; a blocked note keeps
the root ETag unrecorded so it is retried. A fake Nextcloud serves the tree."""
import contextlib
import json
import os
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
os.environ.setdefault("NEXTCLOUD_ADMIN_USER", "corporatetraveldc")

import pytest  # noqa: E402

from poller.skills import second_brain_personal_notes_import as pni  # noqa: E402
from second_brain.scrub_gate import ScrubGateBlocked  # noqa: E402

ROOT_PATH = "/remote.php/dav/files/operator/Notes/"


class FakeNC:
    """notes: relpath -> (content bytes, etag). The root ETag is derived from all of them."""

    def __init__(self, notes):
        self.notes = dict(notes)
        self.gets: list[str] = []
        self.propfinds: list[tuple[str, str]] = []

    def root_etag(self):
        return "r-" + "-".join(sorted(e for _, e in self.notes.values()))

    def _resp(self, href, etag, collection):
        rt = "<d:collection/>" if collection else ""
        return (f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
                f"<d:resourcetype>{rt}</d:resourcetype><d:getetag>\"{etag}\"</d:getetag>"
                f"</d:prop></d:propstat></d:response>")

    def request(self, method, url, headers=None, **kw):
        assert method == "PROPFIND"
        depth = (headers or {}).get("Depth")
        path = urllib.parse.unquote("/" + url.split("://", 1)[1].split("/", 1)[1])
        self.propfinds.append((path, depth))
        rel = path[len(ROOT_PATH):].strip("/")
        parts = [self._resp(path, self.root_etag() if not rel else "d-" + rel, True)]
        if depth == "1":
            seen_dirs = set()
            for relpath, (_, etag) in self.notes.items():
                inner = relpath[len(rel) + 1:] if rel else relpath
                if rel and not relpath.startswith(rel + "/"):
                    continue
                head = inner.split("/", 1)
                if len(head) == 2:
                    d = (rel + "/" if rel else "") + head[0]
                    if d not in seen_dirs:
                        seen_dirs.add(d)
                        parts.append(self._resp(ROOT_PATH + d + "/", "d-" + d, True))
                else:
                    parts.append(self._resp(ROOT_PATH + relpath, etag, False))
        body = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(parts) + "</d:multistatus>"
        return _R(body.encode())

    def get(self, url, **kw):
        rel = urllib.parse.unquote(url.split("/Notes/", 1)[1])
        self.gets.append(rel)
        return _R(self.notes[rel][0])


class _R:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(pni, "STATE_FILE", str(state))
    monkeypatch.setattr(pni, "SRC_PW", "x")
    monkeypatch.setattr(pni, "SRC_BASE", "http://nc.test/remote.php/dav/files")
    monkeypatch.setattr(pni, "SRC_USER", "operator")
    monkeypatch.setattr(pni, "SRC_PATH", "Notes")
    puts: list[str] = []
    monkeypatch.setattr(pni.webdav_client, "put", lambda dest, content: puts.append(dest))
    monkeypatch.setattr(pni, "index_note", lambda *a, **k: None)
    conns: list[int] = []

    @contextlib.contextmanager
    def fake_conn():
        conns.append(1)
        yield object()

    monkeypatch.setattr(pni, "get_conn", fake_conn)
    blocked: set[str] = set()

    def fake_gate(text, source=None):
        if text in blocked:
            raise ScrubGateBlocked("blocked")

    monkeypatch.setattr(pni, "gate", fake_gate)

    def use(nc):
        monkeypatch.setattr(pni.requests, "request", nc.request)
        monkeypatch.setattr(pni.requests, "get", nc.get)

    return {"state": state, "puts": puts, "conns": conns, "use": use, "blocked": blocked}


def test_unchanged_root_downloads_nothing_and_opens_no_db(env):
    nc = FakeNC({"a.md": (b"one", "e1"), "Research - X/b.md": (b"two", "e2")})
    env["use"](nc)
    pni.main()
    assert sorted(nc.gets) == ["Research - X/b.md", "a.md"] and len(env["puts"]) == 2
    nc.gets.clear(); nc.propfinds.clear(); env["conns"].clear()
    pni.main()
    assert nc.gets == [] and env["conns"] == []
    assert nc.propfinds == [(ROOT_PATH, "0")]          # one Depth-0 request, no walk


def test_only_the_changed_note_is_downloaded(env):
    nc = FakeNC({"a.md": (b"one", "e1"), "b.md": (b"two", "e2")})
    env["use"](nc)
    pni.main()
    nc.gets.clear(); env["puts"].clear()
    nc.notes["b.md"] = (b"two v2", "e2b")
    pni.main()
    assert nc.gets == ["b.md"] and len(env["puts"]) == 1


def test_etag_change_with_same_content_is_not_reimported(env):
    nc = FakeNC({"a.md": (b"one", "e1")})
    env["use"](nc)
    pni.main()
    env["puts"].clear()
    nc.notes["a.md"] = (b"one", "e1-touched")          # touched, same bytes
    pni.main()
    assert env["puts"] == []                            # hash still decides
    nc.gets.clear()
    pni.main()
    assert nc.gets == []                                # and the new ETag is remembered


def test_blocked_note_keeps_the_run_retrying(env):
    nc = FakeNC({"a.md": (b"secret", "e1")})
    env["use"](nc)
    env["blocked"].add("secret")
    pni.main()
    assert "_root" not in json.loads(env["state"].read_text())
    env["blocked"].clear()
    pni.main()
    assert len(env["puts"]) == 1


def test_routing_config_change_rewalks(env, monkeypatch):
    nc = FakeNC({"a.md": (b"one", "e1")})
    env["use"](nc)
    pni.main()
    nc.propfinds.clear()
    monkeypatch.setattr(pni, "DEST_NOTES", "01-Sources/elsewhere")
    pni.main()
    assert (ROOT_PATH, "1") in nc.propfinds and len(env["puts"]) == 2


def test_legacy_state_without_etags_still_skips_on_hash(env):
    nc = FakeNC({"a.md": (b"one", "e1")})
    env["use"](nc)
    dest, _ = pni._route("a.md")
    import hashlib
    env["state"].write_text(json.dumps({"a.md": f"{hashlib.sha256(b'one').hexdigest()} {dest}"}))
    pni.main()
    assert env["puts"] == [] and nc.gets == ["a.md"]
