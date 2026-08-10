"""
second_brain.webdav_client -- shared WebDAV client for writing into the
Nextcloud second-brain vault. Extracted 2026-07-22 from the ad hoc scripts
used to build the corporatetraveldc/ business root and PARA folder
scaffolding, so the daily/weekly/manual/RSS ingestion paths share one
tested implementation instead of copy-pasted ones.

Never prints the password. Uses the same app-password secrets file and
Host-header-spoofing approach as second_brain.index_db (Nextcloud validates
Host against trusted_domains even for loopback requests).

2026-08-08: the vault/account split (Option B) was EXECUTED. The vault now
lives under a dedicated non-admin "corporatetraveldc" Nextcloud account. The
business folder stays nested (BUSINESS_ROOT="corporatetraveldc"), so the live
DAV path is files/corporatetraveldc/corporatetraveldc/ -- i.e. only the account
changed, not the folder layout. Because this module is entirely env-driven
(NEXTCLOUD_ADMIN_USER), NO code change was needed: the cutover was a pure env
flip in dispatch.env (NEXTCLOUD_ADMIN_USER=corporatetraveldc) + a fresh
app-password for that account in dispatch-secrets.env.
(The earlier 2026-08-06 note about a "flattened BUSINESS_ROOT" split that was
drafted-but-never-run is superseded: we deliberately kept the folder nested and
only swapped the account, which is why the same code path serves both eras.)

2026-08-09: NEXTCLOUD_ADMIN_USER's silent "operator" default was removed (see
_require_nextcloud_user() below) after it caused a real incident: an ad hoc
interactive run of second_brain.remember without dispatch.env sourced wrote a
genuine vault note to operator's (retired) account instead of corporatetraveldc's,
with no error anywhere -- the write succeeded, the read-back succeeded, it just
silently went to the wrong account. Recovered by hand (copied the file into
the correct account's data dir, removed the stray copy, occ files:scan on
both). Now raises immediately at import if the env var isn't set, rather than
guessing which account you meant.
"""
import os
from xml.etree import ElementTree as ET

import requests

def _require_nextcloud_user() -> str:
    """No silent fallback (2026-08-09 postmortem): a `NEXTCLOUD_ADMIN_USER`-less
    interactive/ad hoc invocation of this module used to default to "operator" --
    the account, RETIRED 2026-08-08 by the vault/account split -- and silently
    wrote a real vault note there instead of the current corporatetraveldc
    account. The write itself succeeded (no error anywhere), so nothing caught
    it; the note only turned up missing when someone went looking for it in
    the UI. Fail loudly at import time instead: production containers already
    get this from dispatch.env's EnvironmentFile, so this only ever fires for
    the ad hoc case that needs it."""
    user = os.environ.get("NEXTCLOUD_ADMIN_USER")
    if not user:
        raise RuntimeError(
            "NEXTCLOUD_ADMIN_USER is not set. This used to silently default to "
            "the retired 'operator' account (see this function's docstring for why "
            "that's exactly the failure mode being avoided) -- source "
            "/etc/corporatetraveldc/dispatch.env, or export "
            "NEXTCLOUD_ADMIN_USER=corporatetraveldc explicitly, before importing "
            "second_brain.webdav_client."
        )
    return user


WEBDAV_BASE = os.environ.get("NEXTCLOUD_WEBDAV_BASE", "http://127.0.0.1:8090/remote.php/dav/files")
NEXTCLOUD_USER = _require_nextcloud_user()
_SECRETS_FILE = os.environ.get(
    "NEXTCLOUD_SECRETS_FILE",
    "/etc/corporatetraveldc/dispatch-secrets.env",
)
HOST_HEADER = "cloud.example.com"
BUSINESS_ROOT = "corporatetraveldc"

_DAV_NS = "{DAV:}"


def _load_password() -> str | None:
    """Prefer the NEXTCLOUD_APP_PASSWORD env var (how every container-invoked
    skill gets it -- threaded via the same EnvironmentFile=dispatch-secrets.env
    every other Quadlet already uses), falling back to reading the secrets
    file directly for host-direct invocation (e.g. `python3 -m
    second_brain.index_db --scan` run straight over SSH, matching how this
    module has been used since 2026-07-20). Never prints the value either way.
    """
    env_pw = os.environ.get("NEXTCLOUD_APP_PASSWORD")
    if env_pw:
        return env_pw
    if not os.path.exists(_SECRETS_FILE):
        return None
    with open(_SECRETS_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("NEXTCLOUD_APP_PASSWORD="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _auth() -> tuple[str, str]:
    pw = _load_password()
    if not pw:
        raise RuntimeError(f"could not read NEXTCLOUD_APP_PASSWORD from {_SECRETS_FILE}")
    return (NEXTCLOUD_USER, pw)


def _base_url() -> str:
    return f"{WEBDAV_BASE}/{NEXTCLOUD_USER}"


def mkcol(rel_path: str) -> int:
    """Create a single folder. 201 = created, 405 = already exists (both fine)."""
    url = f"{_base_url()}/{rel_path.strip('/')}"
    r = requests.request("MKCOL", url, auth=_auth(), headers={"Host": HOST_HEADER}, timeout=15)
    return r.status_code


def mkdirs(rel_path: str) -> None:
    """Create every ancestor folder of rel_path that doesn't already exist."""
    parts = rel_path.strip("/").split("/")
    acc: list[str] = []
    for part in parts:
        acc.append(part)
        mkcol("/".join(acc))


def put(rel_path: str, content: str | bytes, content_type: str = "text/markdown") -> int:
    """Write a file, creating parent folders as needed."""
    rel_path = rel_path.strip("/")
    if "/" in rel_path:
        mkdirs(rel_path.rsplit("/", 1)[0])
    if isinstance(content, str):
        content = content.encode("utf-8")
    url = f"{_base_url()}/{rel_path}"
    r = requests.request(
        "PUT", url, auth=_auth(), data=content,
        headers={"Host": HOST_HEADER, "Content-Type": content_type},
        timeout=30,
    )
    r.raise_for_status()
    return r.status_code


def get(rel_path: str) -> bytes | None:
    """Fetch a file's content. Returns None if not found (404)."""
    url = f"{_base_url()}/{rel_path.strip('/')}"
    r = requests.get(url, auth=_auth(), headers={"Host": HOST_HEADER}, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def list_files(rel_path: str = "") -> list[dict]:
    """List files (not folders) directly under rel_path (depth 1, non-recursive)."""
    url = f"{_base_url()}/{rel_path.strip('/')}".rstrip("/")
    body = ('<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/>'
            '<d:getlastmodified/><d:resourcetype/></d:prop></d:propfind>')
    r = requests.request(
        "PROPFIND", url, auth=_auth(), data=body,
        headers={"Host": HOST_HEADER, "Content-Type": "application/xml", "Depth": "1"},
        timeout=15,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)
    marker = f"/remote.php/dav/files/{NEXTCLOUD_USER}/"
    results = []
    for resp in root.findall(f"{_DAV_NS}response"):
        href_el = resp.find(f"{_DAV_NS}href")
        if href_el is None or href_el.text is None:
            continue
        href = requests.utils.unquote(href_el.text)
        if marker not in href:
            continue
        item_path = href.split(marker, 1)[1]
        if not item_path or item_path.rstrip("/") == rel_path.rstrip("/"):
            continue
        propstat = resp.find(f"{_DAV_NS}propstat")
        if propstat is None:
            continue
        prop = propstat.find(f"{_DAV_NS}prop")
        resourcetype = prop.find(f"{_DAV_NS}resourcetype")
        is_dir = resourcetype is not None and resourcetype.find(f"{_DAV_NS}collection") is not None
        if is_dir:
            continue
        results.append({"path": item_path})
    return results
