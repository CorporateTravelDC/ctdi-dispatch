#!/usr/bin/env python3
"""scripts/build/build-policy.py -- static build-policy checks (2026-10-07,
security review 05; docs/REPRODUCIBLE_BUILDS.md). Offline; reads only the
repository. Run by provenance verification, the integrity sweep and the tests.

Checks (each VERIFIED / FAILED / NOT APPLICABLE):
  locks          every lock in build/policy.toml present, hashed, current with its .in
  base-images    every FROM fully qualified + pinned to a digest; one digest per name:tag
  python-install every `pip install` in a Containerfile is the canonical locked install
                 of a lock this Containerfile is a declared consumer of (and COPYs)
  os-packages    every apt-get runs under the APT_SNAPSHOT pin; one snapshot everywhere
  node           frontend builds use `npm ci` with a committed lockfile
  downloads      no curl/wget/ADD-URL fetch without a sha256 check, no stray pip
                 install, in any scanned build path (listed exceptions excepted)
  build-commands rollout/build scripts pass no --build-arg overrides

Exit 1 if any check FAILED.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buildlib  # noqa: E402

V, F, NA = "VERIFIED", "FAILED", "NOT APPLICABLE"
BUILD_SCRIPTS = ["build-images.sh", "scripts/stack-refresh.sh", "scripts/serialized-rollout.sh"]


def containerfiles(root: Path, policy: dict) -> list[Path]:
    pats = [p for p in policy.get("downloads", {}).get("scan", []) if "Containerfile" in p]
    out = set()
    for pat in pats:
        out.update(x for x in root.glob(pat) if x.is_file())
    return sorted(out)


def context_of(root: Path, cf: Path) -> Path:
    # Containerfiles at the root build with the root as context; one inside a
    # component directory builds with that directory (the rollout lists agree).
    return root if cf.parent == root else cf.parent


def check_locks(root: Path, policy: dict) -> list[tuple[str, str]]:
    out = []
    for l in policy.get("python", {}).get("lock", []):
        lock = root / l["lock"]
        if not lock.is_file():
            out.append((F, f"{l['lock']}: missing"))
            continue
        text = lock.read_text()
        entries, problems = buildlib.parse_lock(text)
        if buildlib.recorded_input_digest(text) != buildlib.input_digest(root, l["input"]):
            out.append((F, f"{l['lock']}: STALE -- {l['input']} changed without regenerating the lock"))
        elif problems:
            out.append((F, f"{l['lock']}: {problems[0]}"))
        else:
            out.append((V, f"{l['lock']}: {len(entries)} packages hashed, current"))
    return out or [(NA, "no Python locks declared")]


def check_base_images(root: Path, policy: dict) -> list[tuple[str, str]]:
    bp = policy.get("base_images", {})
    out, seen = [], {}
    for cf in containerfiles(root, policy):
        rel = cf.relative_to(root)
        for f in buildlib.parse_from_lines(cf.read_text()):
            problems = []
            if bp.get("require_fully_qualified") and not buildlib.is_fully_qualified(f.name):
                problems.append("not fully qualified (short names resolve through the host's registries.conf)")
            if bp.get("require_digest") and not (f.digest and buildlib.DIGEST_RE.match(f.digest)):
                problems.append("no immutable digest (a tag is not an identity)")
            if f.name.startswith("localhost/"):
                problems.append("builds FROM a local tag")
            key = f"{f.name}:{f.tag}"
            if f.digest and seen.setdefault(key, (f.digest, rel))[0] != f.digest:
                problems.append(f"{key} pinned to a different digest than in {seen[key][1]}")
            out.append((F, f"{rel}:{f.lineno} {f.ref}: " + "; ".join(problems)) if problems
                       else (V, f"{rel}:{f.lineno} {f.name}:{f.tag}@{f.digest[:19]}"))
    return out or [(NA, "no Containerfiles")]


def check_python_install(root: Path, policy: dict) -> list[tuple[str, str]]:
    flags = policy.get("python", {}).get("install_flags", [])
    consumer_locks: dict[str, list[str]] = {}
    for l in policy.get("python", {}).get("lock", []):
        for c in l.get("consumers", []):
            consumer_locks.setdefault(c, []).append(l["lock"])
    out = []
    for cf in containerfiles(root, policy):
        rel = str(cf.relative_to(root))
        ctx = context_of(root, cf)
        lines = buildlib.logical_lines(cf.read_text())
        copies: dict[str, str] = {}   # destination name -> source path relative to the repo
        for _, ln in lines:
            m = re.match(r"^\s*COPY\s+(?!--from)(?:--\S+\s+)*(\S+)\s+(\S+)\s*$", ln, re.I)
            if m:
                src = (ctx / m.group(1)).resolve()
                dest = m.group(2).rstrip("/")
                name = Path(m.group(1)).name if dest in (".", "./") else Path(dest).name
                try:
                    copies[name] = str(src.relative_to(root.resolve()))
                except ValueError:
                    pass
        found = False
        for no, ln in lines:
            for cmd in re.split(r"&&|;", ln):
                if not re.search(r"\bpip3?\s+install\b|python3?\s+-m\s+pip\s+install", cmd):
                    continue
                found = True
                toks = cmd.split()
                rfile = toks[toks.index("-r") + 1] if "-r" in toks and toks.index("-r") + 1 < len(toks) else None
                extra = [t for t in toks[toks.index("install") + 1:] if not t.startswith("-") and t != rfile]
                missing = [f for f in flags if f not in toks]
                src = copies.get(Path(rfile).name) if rfile else None
                allowed = consumer_locks.get(rel, [])
                if missing or extra or not rfile:
                    out.append((F, f"{rel}:{no} pip install is not the canonical locked install "
                                   f"(missing {missing or '-'}; package args {extra or '-'})"))
                elif src not in allowed:
                    out.append((F, f"{rel}:{no} installs {rfile} copied from {src}, which is not a lock "
                                   f"declared for this Containerfile in build/policy.toml ({allowed or 'none'})"))
                else:
                    out.append((V, f"{rel}:{no} installs {src} (hash-locked)"))
        if not found and rel in consumer_locks:
            out.append((F, f"{rel}: declared consumer of {consumer_locks[rel]} but installs nothing from it"))
    return out or [(NA, "no pip installs")]


def check_os_packages(root: Path, policy: dict) -> list[tuple[str, str]]:
    arg = policy.get("os_packages", {}).get("apt_snapshot_arg", "APT_SNAPSHOT")
    out, snaps = [], {}
    for cf in containerfiles(root, policy):
        rel = cf.relative_to(root)
        text = cf.read_text()
        args = buildlib.containerfile_args(text)
        for no, ln in buildlib.logical_lines(text):
            if not re.search(r"\bapt-get\s+(update|install)|\bapt\s+(update|install)", ln):
                continue
            pinned = f"APT::Snapshot" in ln and f"${{{arg}}}" in ln and \
                ln.find("APT::Snapshot") < ln.find("update")
            val = args.get(arg)
            if not pinned or not val or not re.match(r"^\d{8}T\d{6}Z$", val):
                out.append((F, f"{rel}:{no} apt runs against the live mirror (no {arg} snapshot pin before update)"))
            else:
                snaps[str(rel)] = val
                out.append((V, f"{rel}:{no} apt from snapshot {val}"))
        if re.search(r"\b(apk add|dnf install|yum install|microdnf install)\b", text):
            out.append((F, f"{rel}: OS package manager without a snapshot policy (apk/dnf)"))
    if policy.get("os_packages", {}).get("require_single_snapshot") and len(set(snaps.values())) > 1:
        out.append((F, f"different {arg} values: {sorted(set(snaps.values()))}"))
    return out or [(NA, "no OS package installs")]


def check_node(root: Path, policy: dict) -> list[tuple[str, str]]:
    out = []
    for lf in policy.get("node", {}).get("lockfiles", []):
        p = root / lf
        out.append((V, f"{lf} committed") if p.is_file() else (F, f"{lf} missing"))
    for cf in containerfiles(root, policy):
        for no, ln in buildlib.logical_lines(cf.read_text()):
            if re.search(r"\bnpm\s+(install|i)\b", ln):
                out.append((F, f"{cf.relative_to(root)}:{no} npm install (re-resolves); use npm ci"))
            elif re.search(r"\b(yarn|pnpm)\s+(install|add)\b", ln) and "--frozen-lockfile" not in ln:
                out.append((F, f"{cf.relative_to(root)}:{no} yarn/pnpm without --frozen-lockfile"))
            elif re.search(r"\bnpm\s+ci\b", ln):
                out.append((V, f"{cf.relative_to(root)}:{no} npm ci"))
    return out or [(NA, "no Node builds")]


def check_downloads(root: Path, policy: dict) -> list[tuple[str, str]]:
    exc = {e["path"]: e for e in policy.get("exception", [])}
    out = []
    files = set()
    for pat in policy.get("downloads", {}).get("scan", []):
        files.update(x for x in root.glob(pat) if x.is_file())
    for f in sorted(files):
        rel = str(f.relative_to(root))
        is_cf = "Containerfile" in f.name
        text = f.read_text(errors="replace")
        lines = buildlib.logical_lines(text) if is_cf else [
            (i, l) for i, l in enumerate(text.splitlines(), 1) if not l.lstrip().startswith("#")]
        hits = []
        for no, ln in lines:
            if re.match(r"^\s*(echo|log|printf|info|ok|warn|die|Write-\w+)\b", ln):
                continue  # help text that mentions a command, not a fetch
            fetch = (is_cf and re.search(r"\b(curl|wget)\b", ln)) or \
                re.search(r"\bwget\b(?!.*--spider)", ln) or \
                re.search(r"\bcurl\b.*(\s-[a-zA-Z]*[oO]\b|--output|--remote-name|>\s*\S|\|)", ln) or \
                re.search(r"\b(Invoke-WebRequest|iwr)\b", ln)
            if fetch and "sha256sum" not in ln and "Get-FileHash" not in ln and "/dev/null" not in ln:
                hits.append(f"{rel}:{no} network fetch without a digest check")
            if re.match(r"^\s*ADD\s+https?://", ln, re.I) and "--checksum" not in ln:
                hits.append(f"{rel}:{no} ADD <url> without --checksum")
            if not is_cf and re.search(r"\bpip3?\s+install\b", ln) and "--require-hashes" not in ln:
                hits.append(f"{rel}:{no} pip install outside the locked path")
            if re.search(r"\|\s*(ba)?sh\b", ln) and re.search(r"\b(curl|wget)\b", ln):
                hits.append(f"{rel}:{no} pipes a download into a shell")
        if hits and rel in exc:
            out.append((NA, f"{rel}: {len(hits)} finding(s) excepted since {exc[rel]['since']} -- {exc[rel]['reason'][:90]}"))
        elif hits:
            out += [(F, h) for h in hits]
        else:
            out.append((V, f"{rel}: no unverified fetch"))
    for path in exc:
        if not (root / path).exists():
            out.append((F, f"exception for {path} names a file that no longer exists (remove the exception)"))
    return out


def check_build_commands(root: Path, policy: dict) -> list[tuple[str, str]]:
    out = []
    for s in BUILD_SCRIPTS:
        p = root / s
        if not p.is_file():
            continue
        bad = [i for i, l in enumerate(p.read_text().splitlines(), 1)
               if "podman build" in l and not l.lstrip().startswith("#") and "--build-arg" in l]
        out.append((F, f"{s}: --build-arg on lines {bad} can override pinned build inputs") if bad
                   else (V, f"{s}: no build-arg overrides"))
    return out or [(NA, "no build scripts")]


CHECKS = [("locks", check_locks), ("base-images", check_base_images), ("python-install", check_python_install),
          ("os-packages", check_os_packages), ("node", check_node), ("downloads", check_downloads),
          ("build-commands", check_build_commands)]


def run_all(root: Path) -> dict[str, list[tuple[str, str]]]:
    policy = buildlib.load_policy(root)
    return {name: fn(root, policy) for name, fn in CHECKS}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="print only FAILED lines and the summary")
    a = ap.parse_args(argv)
    root = (a.root or buildlib.repo_root()).resolve()
    res = run_all(root)
    failed = sum(1 for rows in res.values() for s, _ in rows if s == F)
    if a.json:
        print(json.dumps({k: [{"status": s, "detail": d} for s, d in v] for k, v in res.items()}, indent=1))
    else:
        for name, rows in res.items():
            worst = F if any(s == F for s, _ in rows) else (V if any(s == V for s, _ in rows) else NA)
            if not a.quiet or worst == F:
                print(f"{worst:15} {name}")
            for s, d in rows:
                if not a.quiet or s == F:
                    print(f"    {s:15} {d}")
        print(f"build-policy: {'FAILED' if failed else 'VERIFIED'} ({failed} failing check(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
