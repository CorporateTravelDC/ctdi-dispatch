"""scripts/build/buildlib.py -- shared helpers for the build-integrity tooling
(2026-10-07, security review 05; docs/REPRODUCIBLE_BUILDS.md).

Used by lock-python.py, pin-base-images.py, build-policy.py and provenance.py.
Standard library only (Python 3.11+ for tomllib), so the tools run on a bare
host without a venv. Nothing here names a deployment: paths are relative to
the repository root, which every caller passes in.
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

POLICY_REL = "build/policy.toml"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
INPUT_DIGEST_PREFIX = "# input-digest: "


def repo_root(start: Path | None = None) -> Path:
    p = (start or Path(__file__)).resolve()
    for cand in [p, *p.parents]:
        if (cand / POLICY_REL).is_file():
            return cand
    raise SystemExit(f"cannot find {POLICY_REL} above {p}")


def load_policy(root: Path) -> dict:
    return tomllib.loads((root / POLICY_REL).read_text())


def canonical_json(obj) -> bytes:
    """Deterministic serialization for anything that is hashed or compared."""
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------- python locks

_INCLUDE_RE = re.compile(r"^\s*-(r|c|-requirement|-constraint)[\s=]+(\S+)")


def input_closure(root: Path, rel: str) -> list[str]:
    """The .in file plus every file it pulls in with -r/-c, recursively,
    as repository-relative paths in a stable order."""
    seen: list[str] = []

    def walk(r: str) -> None:
        if r in seen:
            return
        seen.append(r)
        base = (root / r).parent
        for ln in (root / r).read_text().splitlines():
            m = _INCLUDE_RE.match(ln)
            if m:
                walk(str((base / m.group(2)).resolve().relative_to(root.resolve())))

    walk(rel)
    return seen


def input_digest(root: Path, rel: str) -> str:
    """sha256 over (path, content) of the whole input closure. Recorded in the
    lock; a lock whose recorded digest differs from this is stale."""
    h = hashlib.sha256()
    for r in input_closure(root, rel):
        data = (root / r).read_bytes()
        h.update(r.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    return "sha256:" + h.hexdigest()


def recorded_input_digest(lock_text: str) -> str | None:
    for ln in lock_text.splitlines():
        if ln.startswith(INPUT_DIGEST_PREFIX):
            return ln[len(INPUT_DIGEST_PREFIX):].strip()
    return None


@dataclass
class LockEntry:
    name: str
    version: str
    markers: str
    hashes: list[str] = field(default_factory=list)


def parse_lock(text: str) -> tuple[list[LockEntry], list[str]]:
    """Parse a hashed requirements lock. Returns (entries, problems): a problem
    is any line that is not a plain `name==version [; markers]` requirement
    with hashes (URLs, editables, index overrides, unhashed pins)."""
    entries: list[LockEntry] = []
    problems: list[str] = []
    joined = re.sub(r"\\\n", " ", text)
    for raw in joined.splitlines():
        ln = raw.split(" #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not ln:
            continue
        if ln.startswith("-"):
            problems.append(f"option line not allowed in a lock: {ln[:60]}")
            continue
        req, _, rest = ln.partition("--hash=")
        req = req.strip()
        hashes = re.findall(r"--hash=(sha256:[0-9a-f]{64})", ln)
        spec, _, markers = req.partition(";")
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s;]+)$", spec.strip())
        if not m:
            problems.append(f"not an exact pin: {spec.strip()[:60]}")
            continue
        if not hashes:
            problems.append(f"no hash: {m.group(1)}=={m.group(3)}")
        entries.append(LockEntry(m.group(1).lower().replace("_", "-"), m.group(3), markers.strip(), hashes))
    return entries, problems


# ----------------------------------------------------------------- Containerfiles

@dataclass
class FromLine:
    lineno: int
    ref: str            # as written, after ARG substitution
    name: str           # registry/path without tag or digest
    tag: str | None
    digest: str | None
    stage_alias: str | None


_FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", re.I)
_ARG_RE = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)(?:=(\S*))?\s*$", re.I)


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Containerfile lines with backslash continuations joined; comments dropped."""
    out, buf, start = [], "", 0
    for i, ln in enumerate(text.splitlines(), 1):
        if not buf and ln.lstrip().startswith("#"):
            continue
        if not buf:
            start = i
        if ln.rstrip().endswith("\\"):
            buf += ln.rstrip()[:-1] + " "
            continue
        buf += ln
        if buf.strip():
            out.append((start, buf))
        buf = ""
    if buf.strip():
        out.append((start, buf))
    return out


def containerfile_args(text: str) -> dict[str, str]:
    args = {}
    for _, ln in logical_lines(text):
        m = _ARG_RE.match(ln)
        if m and m.group(2) is not None:
            args.setdefault(m.group(1), m.group(2))
    return args


def parse_from_lines(text: str) -> list[FromLine]:
    args = containerfile_args(text)
    aliases: set[str] = set()
    out = []
    for no, ln in logical_lines(text):
        m = _FROM_RE.match(ln)
        if not m:
            continue
        ref = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda x: args.get(x.group(1), x.group(0)), m.group(1))
        name, digest = (ref.split("@", 1) + [None])[:2] if "@" in ref else (ref, None)
        tag = None
        last = name.rsplit("/", 1)[-1]
        if ":" in last:
            name, tag = name.rsplit(":", 1)
        out.append(FromLine(no, ref, name, tag, digest, m.group(2)))
        if m.group(2):
            aliases.add(m.group(2).lower())
    # a FROM naming an earlier stage is not an external image
    return [f for f in out if f.name.lower() not in aliases]


def is_fully_qualified(name: str) -> bool:
    first = name.split("/", 1)[0]
    return "/" in name and ("." in first or ":" in first or first == "localhost")
