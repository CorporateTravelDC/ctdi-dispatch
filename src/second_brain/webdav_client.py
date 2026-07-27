"""
second_brain.webdav_client -- shared WebDAV client for writing into the
Nextcloud second-brain vault. Extracted 2026-07-22 from the ad hoc scripts
used to build the corporatetraveldc/ business root and PARA folder
scaffolding, so the daily/weekly/manual/RSS ingestion paths share one
tested implementation instead of copy-pasted ones.

Never prints the password. Uses the same app-password secrets file and
Host-header-spoofing approach as second_brain.index_db (Nextcloud validates
Host against trusted_domains even for loopback requests).
"""
import os
from xml.etree import ElementTree as ET

import requests

WEBDAV_BASE = os.environ.get("NEXTCLOUD_WEBDAV_BASE", "http://127.0.0.1:8090/remote.php/dav/files")
NEXTCLOUD_USER = os.environ.get("NEXTCLOUD_ADMIN_USER", "corey")
_SECRETS_FILE = os.environ.get(
    "NEXTCLOUD_SECRETS_FILE",
    os.path.expanduser("~/.config/nextcloud/nextcloud-secrets.env"),
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
    url = f"{_base_url()}/{rel_path}"
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
    if "/" in rel_path.strip("/"):
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
    url = f"{_base_url()}/{rel_path}"
    r = requests.get(url, auth=_auth(), headers={"Host": HOST_HEADER}, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def list_files(rel_path: str = "") -> list[dict]:
    """List files (not folders) directly under rel_path (depth 1, non-recursive)."""
    url = f"{_base_url()}/{rel_path}".rstrip("/")
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
