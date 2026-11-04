#!/usr/bin/env python3
"""
scripts/handoff-mirror.py -- the bilateral /srv <-> vault handoff mirror (operator 2026-11-03,
standing rule: vault 01-Sources/manual/HANDOFF-MIRROR-STANDING-RULE.md once adopted).

Cloud-gateway accounts (Cowork, ChatGPT) have no Unix session, so they can never read /srv; on-box
accounts read the vault only through the research route. This keeps both sides in step:

OUT (box -> vault): text drafts (.md/.txt) that on-box accounts drop in
    /srv/ctdc-handoff/<name>-from-<account>/ and anything in /srv/ctdc-handoff/research-intake/
    (except the IN folder below) are copied, scrub-gated, create-only, to
    01-Sources/personal-notes/Series/handoff/from-box/<YYYY-MM-DD>/<drop>/<file>.md
    (inside the research read scope). Code patches, archives and binaries stay on the box; they are
    listed in the day's index by sha256 only.
IN (vault -> box): contributions by the cloud accounts (HANDOFF_MIRROR_CLOUD_ACCOUNTS) under
    01-Sources/personal-notes/Series/contributions/<task>/<account>/ are copied to
    /srv/ctdc-handoff/research-intake/from-vault/<account>/<task>/<file>.md, so research-intake is
    the one bilateral folder.

Pointers: Series/handoff/from-box/CURRENT.md is rewritten each run that mirrors something (it names
the newest dated folder and lists what is in it); research-intake/from-vault/CURRENT.md the same for
IN. Everything else is create-only.

Idempotent via a content-hash state file; never deletes either side. Every OUT body passes the
CUI/PII scrub gate, and registration-shaped tokens are withheld (LADD status is not checked here).
No model call. Light (a few PROPFINDs and small files), but still skips while load1 >= the relay
gate so it never adds to a heavy window.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import urllib.parse
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from second_brain import webdav_client  # noqa: E402
from second_brain.scrub_gate import ScrubGateBlocked, gate  # noqa: E402

log = logging.getLogger(__name__)

SKILL = "handoff-mirror"
HANDOFF = Path(os.getenv("HANDOFF_MIRROR_SRV", "/srv/ctdc-handoff"))
INTAKE = HANDOFF / "research-intake"
IN_DIR = INTAKE / "from-vault"
OUT_ROOT = os.getenv("HANDOFF_MIRROR_VAULT_OUT",
                     "01-Sources/personal-notes/Series/handoff/from-box").strip("/")
CONTRIB_ROOT = os.getenv("HANDOFF_MIRROR_VAULT_IN",
                         "01-Sources/personal-notes/Series/contributions").strip("/")
CLOUD_ACCOUNTS = tuple(a.strip() for a in os.getenv(
    "HANDOFF_MIRROR_CLOUD_ACCOUNTS", "ctdc-agent-anthropic-cowork").split(",") if a.strip())
STATE = os.getenv("HANDOFF_MIRROR_STATE",
                  str(Path.home() / ".local/state/corporatetraveldc/handoff_mirror_state.json"))
TEXT_SUFFIXES = (".md", ".txt")
MAX_BYTES = int(os.getenv("HANDOFF_MIRROR_MAX_BYTES", "262144"))
LOAD_MAX = float(os.getenv("HANDOFF_MIRROR_LOAD_MAX", "24"))
# Start line: files older than this are not mirrored (the 2026-11-03 one-time Codex mirror already
# covered the backlog; contributions are named <UTC>-<slug>.md, so the IN side compares names).
SINCE = os.getenv("HANDOFF_MIRROR_SINCE", "2026-11-03T00:00:00Z")
_SINCE_EPOCH = datetime.datetime.fromisoformat(SINCE.replace("Z", "+00:00")).timestamp()
_SINCE_NAME = SINCE.replace("-", "").replace(":", "")[:15]          # 20261103T000000
_DAV = "{DAV:}"
_DROP_RX = re.compile(r"^(?P<name>.+)-from-(?P<account>[a-z0-9-]+)$")
_REG_RX = re.compile(rb"\bN[1-9][0-9]{0,4}[A-HJ-NP-Z]{0,2}\b")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _load_state() -> dict:
    try:
        return json.loads(Path(STATE).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(s: dict) -> None:
    Path(STATE).parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE + ".tmp"
    Path(tmp).write_text(json.dumps(s, sort_keys=True))
    os.replace(tmp, STATE)


def _withhold_registrations(b: bytes) -> tuple[bytes, int]:
    n = len(_REG_RX.findall(b))
    return (_REG_RX.sub(b"[tail withheld]", b), n) if n else (b, 0)


# ── OUT: box -> vault ──────────────────────────────────────────────────────────

def out_candidates() -> list[tuple[str, Path]]:
    """(label, path) for every regular file a cloud agent may need: files in *-from-<account>/
    drops and in research-intake/ (never research-intake/from-vault/, which is the IN side)."""
    found: list[tuple[str, Path]] = []
    if HANDOFF.is_dir():
        for d in sorted(HANDOFF.iterdir()):
            if d.is_dir() and not d.is_symlink() and _DROP_RX.match(d.name):
                for f in sorted(d.rglob("*")):
                    if f.is_file() and not f.is_symlink() and f.stat().st_mtime >= _SINCE_EPOCH:
                        found.append((d.name, f))
    if INTAKE.is_dir():
        for f in sorted(INTAKE.rglob("*")):
            if (f.is_file() and not f.is_symlink() and IN_DIR not in f.parents and f.name != "CURRENT.md"
                    and f.stat().st_mtime >= _SINCE_EPOCH):
                found.append(("research-intake", f))
    return found


def mirror_out(state: dict, today: str) -> list[dict]:
    done = state.setdefault("out", {})
    rows: list[dict] = []
    for label, f in out_candidates():
        try:
            b = f.read_bytes()
        except OSError as e:
            log.warning("%s: unreadable %s (%s)", SKILL, f, type(e).__name__)
            continue
        h = _sha(b)
        key = f"{f}:{h}"
        if key in done:
            continue
        rel = f.relative_to(HANDOFF).as_posix()
        row = {"source": f"/srv/ctdc-handoff/{rel}", "sha256": h, "bytes": len(b), "mirrored": None}
        if f.suffix.lower() in TEXT_SUFFIXES and len(b) <= MAX_BYTES:
            body, withheld = _withhold_registrations(b)
            text = body.decode("utf-8", "replace")
            try:
                gate(text, source=SKILL)
            except ScrubGateBlocked as e:
                log.error("%s: %s BLOCKED by scrub gate, not mirrored: %s", SKILL, rel, e)
                row["mirrored"] = "withheld (scrub gate)"
                rows.append(row)
                done[key] = "blocked"
                continue
            m = _DROP_RX.match(label)
            origin = m.group("account") if m else "research-intake (on-box)"
            name = _SAFE.sub("-", f.relative_to(HANDOFF / label).as_posix()) if label != "research-intake" \
                else _SAFE.sub("-", f.relative_to(INTAKE).as_posix())
            dest = f"{OUT_ROOT}/{today}/{_SAFE.sub('-', label)}/{Path(name).stem}.md"
            doc = (f"# Mirror: {rel}\n\n- Source: `/srv/ctdc-handoff/{rel}`\n- Origin account: {origin}\n"
                   f"- sha256 of the source file: `{h}`\n- Mirrored: {_now():%Y-%m-%dT%H:%MZ} by {SKILL}\n"
                   + (f"- Withheld: {withheld} registration-shaped token(s); the sha256 is of the source.\n"
                      if withheld else "")
                   + "\n---\n\n" + text)
            try:
                webdav_client.put_create_only(f"{webdav_client.BUSINESS_ROOT}/{dest}", doc)
            except webdav_client.AlreadyExists:
                pass
            row["mirrored"] = dest
        else:
            row["mirrored"] = "on the box only"
        rows.append(row)
        done[key] = row["mirrored"]
    return rows


def write_out_index(rows: list[dict], today: str) -> None:
    if not rows:
        return
    lines = [f"# Handoff mirror (box -> vault): CURRENT\n",
             f"Newest folder: `{OUT_ROOT}/{today}/`. Updated {_now():%Y-%m-%dT%H:%MZ} by {SKILL}.",
             "Text drafts are mirrored there; patches, archives and binaries stay on the box (sha256 below).\n",
             "| source | bytes | sha256 | mirrored as |", "|---|---:|---|---|"]
    for r in rows:
        lines.append(f"| `{r['source']}` | {r['bytes']} | `{r['sha256'][:16]}…` | {r['mirrored']} |")
    index = "\n".join(lines) + "\n"
    try:
        webdav_client.put_create_only(
            f"{webdav_client.BUSINESS_ROOT}/{OUT_ROOT}/{today}/INDEX-{_now():%H%M%S}.md", index)
    except webdav_client.AlreadyExists:
        pass
    webdav_client.put(f"{webdav_client.BUSINESS_ROOT}/{OUT_ROOT}/CURRENT.md", index)


# ── IN: vault -> box ───────────────────────────────────────────────────────────

def _propfind(rel: str) -> list[tuple[str, bool]]:
    url = f"{webdav_client._base_url()}/{webdav_client.BUSINESS_ROOT}/{urllib.parse.quote(rel.strip('/'))}/"
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:resourcetype/></d:prop></d:propfind>')
    r = requests.request("PROPFIND", url, auth=webdav_client._auth(), data=body,
                         headers={"Host": webdav_client.HOST_HEADER, "Depth": "1",
                                  "Content-Type": "application/xml"}, timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    marker = f"/{webdav_client.BUSINESS_ROOT}/{rel.strip('/')}/"
    out = []
    for resp in ET.fromstring(r.content).findall(f"{_DAV}response"):
        href_el = resp.find(f"{_DAV}href")
        if href_el is None or not href_el.text:
            continue
        href = urllib.parse.unquote(href_el.text)
        if marker not in href:
            continue
        name = href.split(marker, 1)[1].strip("/")
        if not name or "/" in name:
            continue
        rt = resp.find(f"{_DAV}propstat/{_DAV}prop/{_DAV}resourcetype")
        out.append((name, rt is not None and rt.find(f"{_DAV}collection") is not None))
    return out


def in_candidates() -> list[tuple[str, str, str]]:
    """(account, task, vault path) for every cloud-account contribution file."""
    found = []
    for task, is_dir in _propfind(CONTRIB_ROOT):
        if not is_dir:
            continue
        for acct, adir in _propfind(f"{CONTRIB_ROOT}/{task}"):
            if not adir or acct not in CLOUD_ACCOUNTS:
                continue
            for fname, fdir in _propfind(f"{CONTRIB_ROOT}/{task}/{acct}"):
                if not fdir and fname.lower().endswith(".md") and fname[:15] >= _SINCE_NAME:
                    found.append((acct, task, f"{CONTRIB_ROOT}/{task}/{acct}/{fname}"))
    return found


def mirror_in(state: dict) -> list[dict]:
    done = state.setdefault("in", {})
    rows = []
    for acct, task, vpath in in_candidates():
        if vpath in done:
            continue
        b = webdav_client.get(f"{webdav_client.BUSINESS_ROOT}/{vpath}")
        if b is None:
            continue
        dest = IN_DIR / _SAFE.sub("-", acct) / _SAFE.sub("-", task) / _SAFE.sub("-", Path(vpath).name)
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o2775)
        if not dest.exists():
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(b)
            os.chmod(tmp, 0o664)
            os.replace(tmp, dest)
        rows.append({"vault": vpath, "file": str(dest), "sha256": _sha(b), "bytes": len(b)})
        done[vpath] = _sha(b)
    return rows


def write_in_index(rows: list[dict]) -> None:
    if not rows:
        return
    lines = [f"# Handoff mirror (vault -> box): CURRENT\n",
             f"Updated {_now():%Y-%m-%dT%H:%MZ} by {SKILL}. New this run:\n",
             "| vault source | box file | bytes | sha256 |", "|---|---|---:|---|"]
    for r in rows:
        lines.append(f"| `{r['vault']}` | `{r['file']}` | {r['bytes']} | `{r['sha256'][:16]}…` |")
    IN_DIR.mkdir(parents=True, exist_ok=True, mode=0o2775)
    tmp = IN_DIR / "CURRENT.md.tmp"
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o664)
    os.replace(tmp, IN_DIR / "CURRENT.md")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load1 = os.getloadavg()[0]
    if load1 >= LOAD_MAX:
        log.info("%s: load1 %.1f >= %.0f, skipped this run", SKILL, load1, LOAD_MAX)
        return 0
    state = _load_state()
    today = _now().strftime("%Y-%m-%d")
    out_rows = mirror_out(state, today)
    write_out_index(out_rows, today)
    in_rows = mirror_in(state)
    write_in_index(in_rows)
    _save_state(state)
    log.info("%s: OUT %d new (%d mirrored as text), IN %d new", SKILL, len(out_rows),
             sum(1 for r in out_rows if str(r["mirrored"]).startswith(OUT_ROOT)), len(in_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
