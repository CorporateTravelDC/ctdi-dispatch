#!/usr/bin/python3 -I
"""scripts/approved-exec.py -- run ONE human-approved privileged command as root.

Installed root-owned at /usr/local/libexec/ctdc/approved-exec.py by
scripts/install-root-copies.sh (hash-checked against the signed manifest) and
reachable through exactly one sudoers rule:

    <operator> ALL=(root) NOPASSWD: /usr/local/libexec/ctdc/approved-exec.py

It replaces the direct NOPASSWD rules for the gated commands (dnf remove,
dnf autoremove, semanage port -a), which let any process running as the
operator account skip the approval entirely (docs/AGENT_TRUST_MODEL.md §9.1).

What it trusts, and why that holds even against code running as the operator:
  * The approval record is fetched from the web API, but NOTHING in it is
    trusted until the human's SSH signature over its canonical text verifies
    against a ROOT-OWNED allowed-signers pin
    (/etc/corporatetraveldc/approval-allowed-signers). A process that can write
    the database can forge a row, but not the approval key's signature.
  * The command executed is the one in the signed row (bound by its sha256), it
    must fullmatch a ROOT-OWNED allowlist entry for its pattern
    (approved-exec.conf beside this file), and it runs without a shell.
  * Each approval executes at most once: a root-only ledger
    (/var/lib/ctdc-approved-exec, outside every container mount) records the id
    with O_EXCL before anything runs.
  * Timing: the signature must have been made before the request expired, and
    execution must start within EXEC_GRACE_S of that expiry.

Usage (from scripts/sudo-approval-gate.sh):  sudo -n approved-exec.py <request-id>
Exit codes: the command's own, or 64 usage, 65 refused (with the reason on stderr).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PIN = "/etc/corporatetraveldc/approval-allowed-signers"
ALLOWLIST = os.path.join(HERE, "approved-exec.conf")
LEDGER = "/var/lib/ctdc-approved-exec"
SECRETS = "/etc/corporatetraveldc/dispatch-secrets.env"
API = "http://127.0.0.1:8000/admin/approval-requests/"
NAMESPACE = "corporatetraveldc-approval"
EXEC_GRACE_S = 300
_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class Refused(Exception):
    pass


def _sha(v: str | None) -> str:
    return hashlib.sha256((v or "").encode("utf-8")).hexdigest()


def canonical(row: dict, action: str = "allow") -> bytes:
    """Byte-identical to common.governance.approval_canonical (v2); a test
    keeps the two in lockstep."""
    return "\n".join([
        "corporatetraveldc-approval v2",
        f"id: {row['id']}",
        f"action: {action}",
        f"kind: {row.get('kind') or 'sudo'}",
        f"requester: {row.get('requester') or '-'}",
        f"expires_at: {int(row['expires_at'])}",
        f"command-sha256: {_sha(row.get('command'))}",
        f"pattern-sha256: {_sha(row.get('command_pattern'))}",
        f"reason-sha256: {_sha(row.get('reasoning'))}",
    ]).encode("utf-8")


def _root_only(path: str, want_dir: bool = False) -> None:
    """Refuse a trust file that root does not exclusively control."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise Refused(f"{path} is a symlink")
    if st.st_uid != 0 or (st.st_mode & 0o022):
        raise Refused(f"{path} is not root-owned and non-writable by others")
    if want_dir != stat.S_ISDIR(st.st_mode):
        raise Refused(f"{path} has the wrong type")


def load_allowlist(path: str = ALLOWLIST) -> dict[str, re.Pattern]:
    """'<pattern>\\t<regex>' per line; the regex fullmatches shlex.join(argv)."""
    out = {}
    for ln in open(path, encoding="utf-8"):
        ln = ln.rstrip("\n")
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        pattern, rx = ln.split("\t", 1)
        out[pattern.strip()] = re.compile(rx.strip())
    return out


def command_argv(row: dict, allow: dict[str, re.Pattern]) -> list[str]:
    argv = shlex.split(row.get("command") or "")
    if argv[:1] == ["sudo"]:
        argv = argv[1:]
        while argv[:1] and argv[0].startswith("-"):
            argv = argv[1:]
    rx = allow.get(row.get("command_pattern") or "")
    if rx is None:
        raise Refused(f"pattern {row.get('command_pattern')!r} is not in the root allowlist")
    if not argv or not os.path.isabs(argv[0]) or not rx.fullmatch(shlex.join(argv)):
        raise Refused("command does not match the allowlisted shape for its pattern")
    return argv


def check_row(row: dict, request_id: str, now: float) -> None:
    if row.get("id") != request_id:
        raise Refused("record id mismatch")
    if row.get("status") != "allowed" or (row.get("kind") or "sudo") != "sudo":
        raise Refused(f"request is {row.get('status')} / kind {row.get('kind')}, not an allowed sudo request")
    if not row.get("resolution_sig") or not row.get("resolved_by"):
        raise Refused("no human signature recorded")
    exp = float(row["expires_at"])
    if float(row.get("resolved_at") or 0) > exp:
        raise Refused("signed after the request expired")
    if now > exp + EXEC_GRACE_S:
        raise Refused("too late: execution must start within 5 minutes of the request's expiry")


def verify_signature(row: dict, pin: str = PIN) -> None:
    sig = base64.b64decode(row["resolution_sig"], validate=True)
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "sig")
        with open(sp, "wb") as fh:
            fh.write(sig)
        r = subprocess.run(["/usr/bin/ssh-keygen", "-Y", "verify", "-f", pin, "-I", row["resolved_by"],
                            "-n", NAMESPACE, "-s", sp], input=canonical(row), capture_output=True)
    if r.returncode != 0:
        raise Refused("the approval signature does not verify against the root-owned approval pin")


def consume(request_id: str, ledger: str = LEDGER) -> int:
    try:
        return os.open(os.path.join(ledger, request_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise Refused("this approval has already been executed")


def _fetch(request_id: str) -> dict:
    token = ""
    for ln in open(SECRETS, encoding="utf-8"):
        if ln.startswith("DISPATCH_ADMIN_TOKEN="):
            token = ln.split("=", 1)[1].strip().strip("'\"")
    if not token:
        raise Refused("no admin token available to read the approval record")
    req = urllib.request.Request(API + request_id, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not _ID_RE.match(argv[1]):
        print("usage: approved-exec.py <approval-request-uuid>", file=sys.stderr)
        return 64
    rid = argv[1]
    try:
        if os.geteuid() != 0:
            raise Refused("must run as root (via its sudoers rule)")
        _root_only(PIN)
        _root_only(ALLOWLIST)
        os.makedirs(LEDGER, mode=0o700, exist_ok=True)
        _root_only(LEDGER, want_dir=True)
        row = _fetch(rid)
        check_row(row, rid, time.time())
        cmd = command_argv(row, load_allowlist())
        verify_signature(row)
        fd = consume(rid)
    except Refused as e:
        print(f"approved-exec: REFUSED {rid}: {e}", file=sys.stderr)
        subprocess.run(["/usr/bin/logger", "-t", "approved-exec", f"refused {rid}: {e}"], check=False)
        return 65
    subprocess.run(["/usr/bin/logger", "-t", "approved-exec",
                    f"exec {rid} signer={row['resolved_by']} pattern={row.get('command_pattern')}"], check=False)
    rc = subprocess.run(cmd, env={"PATH": "/usr/sbin:/usr/bin", "LANG": "C.UTF-8"}).returncode
    os.write(fd, json.dumps({"id": rid, "signer": row["resolved_by"], "pattern": row.get("command_pattern"),
                             "argv": cmd, "rc": rc, "at": time.time()}).encode())
    os.close(fd)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
