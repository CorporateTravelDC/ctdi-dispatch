#!/usr/bin/env python3
"""scripts/build/pin-base-images.py -- list, verify and deliberately refresh the
base-image digests and the Debian snapshot pin (2026-10-07, security review 05;
docs/REPRODUCIBLE_BUILDS.md).

A base-image or OS-package update is a reviewable maintenance event: this
tool rewrites the pins in the Containerfiles, prints what moved, and leaves
the diff for review, signing and a normal rebuild. It never builds.

  pin-base-images.py --list                       every FROM and APT_SNAPSHOT
  pin-base-images.py --verify-remote              each pinned digest is a multi-platform
                                                  index that still carries arm64 + amd64
  pin-base-images.py --refresh [NAME:TAG ...]     re-resolve tag(s) to today's index digest
  pin-base-images.py --apt-snapshot now|YYYYMMDDTHHMMSSZ   move the Debian snapshot pin

Needs `skopeo` for --verify-remote / --refresh (network). --list is offline.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buildlib  # noqa: E402

ROOT = buildlib.repo_root()
REQUIRED_ARCHES = {"arm64", "amd64"}


def containerfiles(policy) -> list[Path]:
    out = set()
    for pat in policy.get("downloads", {}).get("scan", []):
        if "Containerfile" in pat:
            out.update(x for x in ROOT.glob(pat) if x.is_file())
    return sorted(out)


def remote_index(ref: str) -> tuple[str, str, set[str]]:
    r = subprocess.run(["skopeo", "inspect", "--raw", f"docker://{ref}"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode().strip()[-300:])
    digest = "sha256:" + hashlib.sha256(r.stdout).hexdigest()
    j = json.loads(r.stdout)
    arches = {m.get("platform", {}).get("architecture") for m in j.get("manifests", [])}
    return digest, j.get("mediaType", ""), {a for a in arches if a and a != "unknown"}


def do_list(policy) -> int:
    for cf in containerfiles(policy):
        text = cf.read_text()
        for f in buildlib.parse_from_lines(text):
            print(f"{cf.relative_to(ROOT)}:{f.lineno}\t{f.name}:{f.tag}\t{f.digest or 'UNPINNED'}")
        snap = buildlib.containerfile_args(text).get("APT_SNAPSHOT")
        if snap:
            print(f"{cf.relative_to(ROOT)}\tAPT_SNAPSHOT\t{snap}")
    return 0


def do_verify_remote(policy) -> int:
    bad = 0
    pins = {}
    for cf in containerfiles(policy):
        for f in buildlib.parse_from_lines(cf.read_text()):
            if f.digest:
                pins[(f.name, f.digest)] = cf.relative_to(ROOT)
    for (name, digest), where in sorted(pins.items()):
        try:
            got, mt, arches = remote_index(f"{name}@{digest}")
        except RuntimeError as e:
            print(f"UNVERIFIED {name}@{digest[:19]} ({where}): registry lookup failed: {e}")
            continue
        ok = "index" in mt or "manifest.list" in mt
        missing = REQUIRED_ARCHES - arches
        if got != digest:
            print(f"FAILED     {name}@{digest[:19]}: registry returned content hashing to {got[:19]}"); bad += 1
        elif not ok:
            print(f"FAILED     {name}@{digest[:19]}: single-architecture manifest ({mt}), not an index"); bad += 1
        elif missing:
            print(f"FAILED     {name}@{digest[:19]}: index lacks {sorted(missing)}"); bad += 1
        else:
            print(f"VERIFIED   {name}@{digest[:19]}: index with {', '.join(sorted(arches))}")
    return 1 if bad else 0


def do_refresh(policy, only: list[str]) -> int:
    changed = 0
    cache: dict[str, str] = {}
    for cf in containerfiles(policy):
        text = cf.read_text()
        new = text
        for f in buildlib.parse_from_lines(text):
            key = f"{f.name}:{f.tag}"
            if only and key not in only:
                continue
            if key not in cache:
                d, mt, arches = remote_index(key)
                if REQUIRED_ARCHES - arches:
                    raise SystemExit(f"{key}: upstream index lacks {sorted(REQUIRED_ARCHES - arches)} -- not pinning")
                cache[key] = d
            if cache[key] != f.digest:
                old_ref = f"{f.name}:{f.tag}@{f.digest}" if f.digest else f"{f.name}:{f.tag}"
                new = new.replace(old_ref, f"{f.name}:{f.tag}@{cache[key]}")
                print(f"{cf.relative_to(ROOT)}: {key} {str(f.digest)[:19]} -> {cache[key][:19]}")
                changed += 1
        if new != text:
            cf.write_text(new)
    print(f"{changed} pin(s) moved" + (" -- review the diff, run the tests, sign, rebuild" if changed else ""))
    return 0


def do_apt_snapshot(policy, value: str) -> int:
    if value == "now":
        value = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not re.match(r"^\d{8}T\d{6}Z$", value):
        raise SystemExit("snapshot must look like 20261007T000000Z")
    n = 0
    for cf in containerfiles(policy):
        text = cf.read_text()
        new = re.sub(r"^(ARG APT_SNAPSHOT=)\S+$", rf"\g<1>{value}", text, flags=re.M)
        if new != text:
            cf.write_text(new); n += 1
            print(f"{cf.relative_to(ROOT)}: APT_SNAPSHOT -> {value}")
    print(f"{n} Containerfile(s) moved to Debian snapshot {value} -- review, sign, rebuild")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--verify-remote", action="store_true")
    g.add_argument("--refresh", nargs="*")
    g.add_argument("--apt-snapshot")
    a = ap.parse_args(argv)
    policy = buildlib.load_policy(ROOT)
    if a.list:
        return do_list(policy)
    if a.verify_remote:
        return do_verify_remote(policy)
    if a.refresh is not None:
        return do_refresh(policy, a.refresh)
    return do_apt_snapshot(policy, a.apt_snapshot)


if __name__ == "__main__":
    sys.exit(main())
