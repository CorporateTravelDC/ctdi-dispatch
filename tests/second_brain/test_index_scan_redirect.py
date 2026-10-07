"""
2026-10-06 root cause: second-brain-index-scan reported 0 files with exit 0
since ~2026-08-09. _walk_webdav stripped the trailing slash; the cloud. vhost
only proxies the vault collection WITH the slash, so nginx 301'd, requests
followed to another host, dropped Authorization, and Nextcloud answered 401.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("NEXTCLOUD_ADMIN_USER", "corporatetraveldc")

import requests  # noqa: E402

from second_brain import index_db  # noqa: E402

USER = index_db.NEXTCLOUD_USER
BASE = f"http://host.containers.internal:80/remote.php/dav/files/{USER}"


def _multistatus(*entries):
    parts = []
    for href, is_dir in entries:
        rt = "<d:collection/>" if is_dir else ""
        extra = "" if is_dir else "<d:getcontentlength>12</d:getcontentlength><d:getetag>\"e\"</d:getetag>"
        parts.append(f"<d:response><d:href>/remote.php/dav/files/{USER}/{href}</d:href><d:propstat><d:prop>"
                     f"<d:resourcetype>{rt}</d:resourcetype>{extra}</d:prop></d:propstat></d:response>")
    return ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(parts) + "</d:multistatus>").encode()


def fake_nginx(method, url, **kw):
    """The cloud. vhost: slashless collection -> 301; with slash -> 207."""
    r = requests.Response()
    r.url = url
    if not url.endswith("/"):
        r.status_code, r.headers["Location"] = 301, f"http://cloud.example.com{url.split(':80', 1)[1]}/"
        return r
    if kw.get("allow_redirects", True):
        raise AssertionError("PROPFIND must not follow redirects")
    r.status_code = 207
    if url.endswith("/corporatetraveldc/"):
        r._content = _multistatus(("corporatetraveldc/", True), ("corporatetraveldc/sub/", True),
                                  ("corporatetraveldc/a.md", False))
    else:
        r._content = _multistatus(("corporatetraveldc/sub/", True), ("corporatetraveldc/sub/b.md", False))
    return r


class IndexScanRedirect(unittest.TestCase):
    def test_collections_requested_with_trailing_slash(self):
        errors: list[str] = []
        with mock.patch.object(index_db.requests, "request", side_effect=fake_nginx) as req:
            files = index_db._walk_webdav(BASE, ("u", "p"), "corporatetraveldc", errors)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(f["path"] for f in files), ["corporatetraveldc/a.md", "corporatetraveldc/sub/b.md"])
        for call in req.call_args_list:
            self.assertTrue(call.args[1].endswith("/"), call.args[1])
            self.assertIs(call.kwargs.get("allow_redirects"), False)

    def test_redirect_is_an_error_not_an_empty_vault(self):
        r = requests.Response(); r.status_code = 301; r.headers["Location"] = "https://elsewhere/"; r.url = "x"
        errors: list[str] = []
        with mock.patch.object(index_db.requests, "request", return_value=r):
            files = index_db._walk_webdav(BASE, ("u", "p"), "corporatetraveldc", errors)
        self.assertEqual(files, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("301", errors[0])
