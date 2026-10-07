#!/usr/bin/env python3
"""scripts/site_public.py -- stage a WEBSITE repo's public tree (2026-10-06).

Driven by scripts/site-public.sh. Site repos are not the platform: their brand
IS the content, so the platform scrub (scrub-public-tree.py, which anonymises
the business) is the wrong policy here. Operator decisions, 2026-10-06:
  * keep the brand, the public domain and public contact addresses;
  * strip infrastructure, but map addresses to RFC documentation ranges instead
    of deleting them, so configs stay a readable reference spec
    (RFC 5737 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24; tailnet host names
    -> RFC 2606 tailnet.example; tunnel UUIDs -> the nil UUID);
  * only files the site repo's PUBLIC-ALLOWLIST names, from the CURRENT tree --
    never git history;
  * Executive Standard: infrastructure + a signed ledger of article hashes and
    dates, never article text.
The stage REFUSES (exit 2, nothing written) if a live secret value, a
secret-shaped string, an unlisted email or a real public IP survives.
Prints paths, rule names and counts only -- never a matched value.

  site_public.py stage <site-repo> <out-dir>
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# -- policy ------------------------------------------------------------------------

TAILNET_HOST_RE = re.compile(rb"\b(?:[a-z0-9-]+\.)?tail[0-9a-f]{6}\.ts\.net\b")
UUID_RE = re.compile(rb"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
IPV4_RE = re.compile(rb"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
EMAIL_RE = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET_SHAPES = {
    "github-token": re.compile(rb"github_pat_[A-Za-z0-9_]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "aws-key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "private-key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "slack/stripe": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}|\bsk_live_[A-Za-z0-9]{16,}"),
    "assigned-secret": re.compile(rb"(?i)\b(api[_-]?token|api[_-]?key|secret|password|passwd)\b\s*[:=]\s*['\"]?"
                                  rb"(?!your|example|changeme|placeholder|<|\$\{|\*)[A-Za-z0-9_\-./+=]{16,}"),
}
NIL_UUID = b"00000000-0000-0000-0000-000000000000"


def _doc_ip(a: int, b: int, c: int, d: int) -> bytes | None:
    """Map an infrastructure address to an RFC 5737 documentation address, or
    None to leave it (loopback, unspecified, already-documentation)."""
    ip = ipaddress.IPv4Address(f"{a}.{b}.{c}.{d}")
    if ip.is_loopback or ip.is_unspecified or any(ip in ipaddress.ip_network(n) for n in
                                                  ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")):
        return None
    if ip in ipaddress.ip_network("100.64.0.0/10"):            # tailnet (CGNAT)
        return f"192.0.2.{d}".encode()
    if ip in ipaddress.ip_network("10.x.x.x/8"):
        return f"198.51.100.{d}".encode()
    if ip in ipaddress.ip_network("192.168.x.x/16") or ip in ipaddress.ip_network("172.x.x.x/12"):
        return f"203.0.113.{d}".encode()
    return None


def scrub(data: bytes, subst: list[tuple[bytes, bytes]] = ()) -> bytes:
    for a, b in subst:                                         # per-site literals (PUBLIC_SUBST)
        data = data.replace(a, b)
    data = TAILNET_HOST_RE.sub(b"tailnet.example", data)
    data = UUID_RE.sub(NIL_UUID, data)

    def ip_sub(m):
        if m.string[m.end():m.end() + 1] == b"/":
            return m.group(0)                  # a CIDR range (e.g. 100.64.0.0/10) is generic, not a host
        try:
            octets = [int(x) for x in m.groups()]
            if any(o > 255 for o in octets):
                return m.group(0)
            new = _doc_ip(*octets)
        except ValueError:
            return m.group(0)
        return new if new else m.group(0)
    return IPV4_RE.sub(ip_sub, data)


def _live_secret_values() -> dict:
    """Reuse the platform scrubber's loader (reads dispatch-secrets.env; values
    stay in memory only)."""
    spec = importlib.util.spec_from_file_location("scrub_public_tree", HERE / "scrub-public-tree.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        return mod._load_live_secret_values()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"[site-public] cannot load live secret values for the leak check: {type(e).__name__} -- refusing")


def gate(path: str, data: bytes, secrets: dict, emails_ok: set, ips_ok: set) -> list[str]:
    bad = []
    line = lambda pos: data[:pos].count(b"\n") + 1
    for name, value in secrets.items():
        i = data.find(value)
        if i >= 0:
            bad.append(f"{path}:{line(i)}: live secret value ({name})")
    for name, rx in SECRET_SHAPES.items():
        for m in rx.finditer(data):
            bad.append(f"{path}:{line(m.start())}: secret-shaped string ({name})")
    for m in EMAIL_RE.finditer(data):
        if m.group(0).decode().lower() not in emails_ok:
            bad.append(f"{path}:{line(m.start())}: email not in PUBLIC_EMAILS")
    for m in IPV4_RE.finditer(data):
        try:
            ip = ipaddress.IPv4Address(m.group(0).decode())
        except ValueError:
            continue
        if ip.is_global and str(ip) not in ips_ok:
            bad.append(f"{path}:{line(m.start())}: public IP not in PUBLIC_IPS")
    if TAILNET_HOST_RE.search(data):
        bad.append(f"{path}: tailnet host name survived")
    return bad


# -- staging -----------------------------------------------------------------------

def _conf(repo: Path) -> dict:
    out = {}
    for ln in (repo / "PUBLIC.conf").read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _allowlist(repo: Path) -> list[tuple[bool, str]]:
    pats = []
    for ln in (repo / "PUBLIC-ALLOWLIST").read_text().splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            pats.append((not ln.startswith("!"), ln.lstrip("!")))
    return pats


def selected(files: list[str], pats: list[tuple[bool, str]]) -> list[str]:
    out = []
    for f in files:
        keep = False
        for include, pat in pats:                      # last matching rule wins, like .gitignore
            if fnmatch.fnmatchcase(f, pat):
                keep = include
        if keep:
            out.append(f)
    return out


def ledger(repo: Path, files: list[str]) -> str:
    rows = []
    for f in sorted(files):
        digest = hashlib.sha256((repo / f).read_bytes()).hexdigest()
        added = subprocess.run(["git", "-C", str(repo), "log", "--diff-filter=A", "--follow", "--format=%aI",
                                "--", f], capture_output=True, text=True).stdout.split()
        rows.append(f"{digest}  {(added[-1] if added else 'unknown')[:10]}  {Path(f).stem}")
    head = ("# Article ledger\n\n"
            "Every Executive Standard article, as SHA-256 of its canonical source, the date it was first\n"
            "committed, and its slug -- without the text. Dates are when the canonical file entered this repository (the 2026-09-08 consolidation for older pieces), not the original Substack publication (the full edition is for members). Signed by the\n"
            "publisher's key (ARTICLE-LEDGER.md.asc). A reader holding an article can check it is the\n"
            "one that was published, unaltered, and when.\n\n```\n")
    return head + "\n".join(rows) + "\n```\n"


def stage(repo: Path, out: Path) -> int:
    conf, pats = _conf(repo), _allowlist(repo)
    files = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True,
                           check=True).stdout.splitlines()
    chosen = selected(files, pats)
    emails_ok = {e.strip().lower() for e in conf.get("PUBLIC_EMAILS", "").split(",") if e.strip()}
    ips_ok = {e.strip() for e in conf.get("PUBLIC_IPS", "").split(",") if e.strip()}
    subst = [tuple(x.encode() for x in pair.split("=>", 1)) for pair in conf.get("PUBLIC_SUBST", "").split(";")
             if "=>" in pair]
    secrets = _live_secret_values()
    staged, bad, binaries = {}, [], 0
    for f in chosen:
        data = (repo / f).read_bytes()
        if b"\0" in data[:8192]:
            if not fnmatch.fnmatchcase(f.lower(), "*.png") and not f.lower().endswith((".ico", ".webp", ".jpg")):
                bad.append(f"{f}: binary file outside the image allow-set")
            staged[f] = data
            binaries += 1
            continue
        clean = scrub(data, subst)
        bad += gate(f, clean, secrets, emails_ok, ips_ok)
        staged[f] = clean
    if conf.get("LEDGER_GLOB"):
        art = [f for f in files if fnmatch.fnmatchcase(f, conf["LEDGER_GLOB"])]
        staged["ARTICLE-LEDGER.md"] = ledger(repo, art).encode()
    if bad:
        print(f"[site-public] REFUSED -- {len(bad)} finding(s); nothing written:", file=sys.stderr)
        for b in bad[:60]:
            print(f"  {b}", file=sys.stderr)
        return 2
    for f, data in staged.items():
        dst = out / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        mode = os.stat(repo / f).st_mode & 0o111 if (repo / f).exists() else 0
        if mode:
            os.chmod(dst, 0o755)
    changed = sum(1 for f in chosen if (repo / f).read_bytes() != staged[f])
    print(f"[site-public] staged {len(staged)} file(s) of {len(files)} tracked ({binaries} binary, "
          f"{changed} rewritten by the address scrub"
          f"{', ledger of %d articles' % len([f for f in files if fnmatch.fnmatchcase(f, conf.get('LEDGER_GLOB', '-'))]) if conf.get('LEDGER_GLOB') else ''})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "stage":
        sys.exit(__doc__)
    sys.exit(stage(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()))
