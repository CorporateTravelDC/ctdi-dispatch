#!/usr/bin/env python3
"""scripts/captive-release-gate.py -- the signed captive-portal release for the DNS leak watch (2026-11-27, operator).

config/dns-leak-watch holds a device that came back from an off-tunnel episode (a hotel or airport Wi-Fi login page,
docs/DNS_LEAK_WATCH.md "Off-tunnel and captive portals"). The hold lifts on its own once the device passes the
connection check for CAPTIVE_MIN_HOLD_MIN minutes, or sooner with the operator's SIGNED release: an approval of kind
`captive-portal-release`, pattern captive-portal-release-<host>, resolved `allowed` with a human SSH signature
(scripts/approve.sh). The root run calls `check` through RELEASE_APPROVAL_CMD as RELEASE_APPROVAL_USER (never root)
and acts only on an exact "ok <id>" line.

`check` does not trust the stored status alone: it re-verifies the resolution signature the way
governance.resolve_signed() did (active approval signer, active HUMAN board signer, not the requester, approval key
separate from every board key, signature over approval_canonical(row, "allow")), and that the signed command names
this host. An agent may REQUEST a release; only a human's signature makes it count.

  captive-release-gate.py request --host H --reason R     a pending request (reused while one waits); prints the sign command
  captive-release-gate.py check   --host H --since EPOCH [--machine MKEY]
                                                          exit 0 + "ok <id>" | exit 1 + "none: <why>"
H may be the device's host name or its tailnet name. A request is bound to the device's machine key and refused
unless the leak watch holds it; replies name the device only by its tag (dev-<10 hex>).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
from pathlib import Path

KIND = "captive-portal-release"
PREFIX = "captive-portal-release-"
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
MAX_AGE_S = 24 * 3600   # a release signed longer ago than this never counts, whatever --since says
# 2026-11-28 (operator): a release was filed under the phone's tailnet name while the leak watch knew it by its host
# name, so the signature lifted nothing. Now the request is resolved on the LIVE tailnet (either name), bound to that
# device's machine key, and checked against the leak watch's held list -- which names devices only by tag (an HMAC of
# the host name under TAG_KEY, dns_leak_watch.tag_of). The signed command carries the real name, the machine key and
# the tag; screens and replies show the tag only.
TAG_KEY = Path(os.environ.get("DNSLEAK_TAG_KEY", "/var/lib/ctdc-dnsleak/hold-tag.key"))
LEAK_LATEST = Path(os.environ.get("DNSLEAK_LATEST", "/var/lib/ctdc-dnsleak/latest.json"))
MKEY_RE = re.compile(r"mkey:[0-9a-f]{64}")


def pattern(host: str) -> str:
    return PREFIX + host


def command(host: str, reason: str, machine: str = "", tag: str = "") -> str:
    """Canonical JSON: this IS the approval's command, so the signature covers the host, its machine key, its tag and
    the reason."""
    d = {"type": KIND, "host": host, "reason": reason}
    if machine:
        d["machine"], d["tag"] = machine, tag
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def _host_ok(host: str) -> bool:
    return bool(HOST_RE.fullmatch(host or ""))


def tag_key(path: Path | None = None) -> bytes | None:
    try:
        k = bytes.fromhex((path or TAG_KEY).read_text().strip())
        return k if len(k) >= 16 else None
    except (OSError, ValueError):
        return None


def tag_of(name: str, key: bytes | None) -> str:
    """= dns_leak_watch.tag_of (a test holds the two equal)."""
    import hashlib
    import hmac
    n = (name or "").strip().lower().encode()
    h = hmac.new(key, n, "sha256").hexdigest() if key else hashlib.sha256(b"ctdc-dnsleak-tag\n" + n).hexdigest()
    return "dev-" + h[:10]


def _run(cmd: list) -> str:
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    return r.stdout if r.returncode == 0 else ""


def tailnet_device(name: str, runner=None) -> dict | None:
    """{host, machine} for the live tailnet peer whose host name OR tailnet name is NAME (case-insensitive), else None."""
    run = runner or _run
    try:
        st = json.loads(run(["tailscale", "status", "--json"]) or "{}")
    except ValueError:
        return None
    want = name.strip().lower()
    for pe in (st.get("Peer") or {}).values():
        host = re.sub(r"[^A-Za-z0-9-]", "-", (pe.get("HostName") or "").strip())[:63]
        alias = (pe.get("DNSName") or "").split(".")[0].strip().lower()
        ips = pe.get("TailscaleIPs") or []
        if host and ips and want in (host.lower(), alias):
            try:
                node = json.loads(run(["tailscale", "whois", "--json", ips[0]]) or "{}").get("Node") or {}
                mk = str(node.get("Machine") or "")
            except ValueError:
                mk = ""
            return {"host": host, "machine": mk if MKEY_RE.fullmatch(mk) else ""}
    return None


def held_tags(path: Path | None = None) -> set | None:
    """Every tag (host name and tailnet name) of a device the leak watch holds now, or None when unreadable."""
    try:
        devs = json.loads((path or LEAK_LATEST).read_text()).get("devices") or {}
    except (OSError, ValueError, AttributeError):
        return None
    out = set()
    for t, d in devs.items():
        if isinstance(d, dict) and (d.get("captive_quarantine") or d.get("quarantined")):
            out |= {t, *(d.get("match") or [])}
    return out


def pending(host: str) -> dict | None:
    from common import db, governance
    governance._ensure_approvals()
    with db.conn() as c:
        ids = [x["id"] for x in c.execute("SELECT id FROM approval_requests WHERE kind = ? AND command_pattern = ? "
                                          "AND status = 'pending' ORDER BY created_at DESC", (KIND, pattern(host))).fetchall()]
    for i in ids:
        row = governance.approval_view(i)          # applies expiry-on-read
        if row and row["status"] == "pending":
            return row
    return None


def request(host: str, reason: str, requester: str | None = None, runner=None, key=None, held=None) -> dict:
    """Create the release request unless one for this device is still pending. Returns {"id", "new", "tag", "note"}.
    ValueError (tags only, never a name) when HOST is no tailnet device, or the leak watch is not holding it."""
    from common import governance
    if not _host_ok(host):
        raise ValueError("bad host name")
    reason = " ".join((reason or "").split())[:300]
    if not reason:
        raise ValueError("a reason is required")
    dev = tailnet_device(host, runner)
    if dev is None:
        raise ValueError("no tailnet device by that name (its host name or its tailnet name)")
    if not dev["machine"]:
        raise ValueError("that device's machine key could not be read; nothing requested")
    key = tag_key() if key is None else key
    tag = tag_of(dev["host"], key)
    held = held_tags() if held is None else held
    note = ""
    if key is None or held is None:
        note = "the leak watch's held list could not be read here: the device's hold is not confirmed"
    elif tag not in held and tag_of(host, key) not in held:
        raise ValueError(f"that device is not held (held now: {', '.join(sorted(held)) or 'none'})")
    p = pending(dev["host"])
    if p:
        return {"id": p["id"], "new": False, "tag": tag, "note": note}
    r = governance.create_approval(pattern(dev["host"]), command(dev["host"], reason, dev["machine"], tag), kind=KIND,
                                   requester=requester or f"operator:{getpass.getuser()}",
                                   reasoning=f"captive-portal release for {tag}: {reason}",
                                   ttl_seconds=governance.approval_ttl(KIND))
    return {"id": r["id"], "new": True, "tag": tag, "note": note}


def verify(row: dict, host: str, machine: str = "") -> str:
    """'' when ROW is a human-signed allow of a release for this device -- by MACHINE key when given (any name), else
    by HOST -- else the reason it is not."""
    from common import board_sign, db, governance
    try:
        cmd = json.loads(row.get("command") or "")
        signed_host = cmd.get("host")
    except (ValueError, AttributeError):
        return "signed command is not a release"
    if row.get("kind") != KIND or row.get("status") != "allowed" or row.get("command_pattern") != pattern(signed_host or ""):
        return "not an allowed release"
    if machine:
        if cmd.get("machine") != machine:
            return "signed command names another machine"
    elif signed_host != host:
        return "signed command names another host"
    signer, sig = row.get("resolved_by"), row.get("resolution_sig")
    if not signer or not sig:
        return "unsigned"
    appr = governance.approval_signer_get(signer)
    if not appr or not appr["active"]:
        return "unknown or inactive approval signer"
    board = db.board_signer_get(signer)
    if not board or not board.get("active") or board.get("kind") != "human":
        return "signer is not an active human account"
    if row.get("requester") and row["requester"] == signer:
        return "the requester signed its own request"
    try:
        _, ablob, _ = board_sign.pubkey_line_to_blob(appr["pubkey"])
        if ablob in governance._board_pubkey_blobs():
            return "approval key equals a board key"
        ok = board_sign.verify(appr["pubkey"], sig, governance.approval_canonical(row, "allow"), signer,
                               namespace=governance.NAMESPACE_APPROVAL)
    except board_sign.BoardSignError:
        return "signature rejected"
    return "" if ok else "signature does not verify"


def check(host: str, since: float, now: float | None = None, machine: str = "") -> tuple[dict | None, str]:
    """(row, '') for the newest verified release resolved at or after SINCE (and within MAX_AGE_S) of the device with
    MACHINE key (whatever name it was filed under), or, without MACHINE, of HOST; else (None, why)."""
    from common import db, governance
    if not _host_ok(host):
        return None, "bad host name"
    if machine and not MKEY_RE.fullmatch(machine):
        return None, "bad machine key"
    now = time.time() if now is None else now
    governance._ensure_approvals()
    lo = max(float(since), now - MAX_AGE_S)
    with db.conn() as c:
        if machine:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM approval_requests WHERE kind = ? AND status = 'allowed' AND resolved_by IS NOT NULL "
                "AND resolution_sig IS NOT NULL AND resolved_at >= ? AND resolved_at <= ? ORDER BY resolved_at DESC "
                "LIMIT 20", (KIND, lo, now + 60)).fetchall()]
        else:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM approval_requests WHERE kind = ? AND command_pattern = ? AND status = 'allowed' "
                "AND resolved_by IS NOT NULL AND resolution_sig IS NOT NULL AND resolved_at >= ? AND resolved_at <= ? "
                "ORDER BY resolved_at DESC LIMIT 5", (KIND, pattern(host), lo, now + 60)).fetchall()]
    why = "no signed release since the hold began"
    for row in rows:
        why = verify(row, host, machine)
        if not why:
            return row, ""
    return None, why


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("request"); q.add_argument("--host", required=True); q.add_argument("--reason", required=True)
    c = sub.add_parser("check"); c.add_argument("--host", required=True); c.add_argument("--since", type=int, required=True)
    c.add_argument("--machine", default="")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        try:
            row, why = check(a.host, a.since, machine=a.machine)
        except Exception as e:  # noqa: BLE001 -- the root run reads stdout only; any failure is "no release"
            row, why = None, f"lookup failed ({type(e).__name__})"
        if not row:
            print(f"none: {why}")
            return 1
        print(f"ok {row['id']}")
        return 0
    try:
        r = request(a.host, a.reason)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 65
    if r["note"]:
        print(f"({r['note']})")
    print(f"{'requested' if r['new'] else 'already pending'} {r['id']} for {r['tag']}; "
          f"sign with: scripts/approve.sh allow {r['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
