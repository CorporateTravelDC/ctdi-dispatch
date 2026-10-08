#!/usr/bin/env python3
"""scripts/build/provenance.py -- SBOM + build-provenance receipts for locally
built images, and the verifier that checks them (2026-10-07, security review
05; docs/REPRODUCIBLE_BUILDS.md).

  record  --image REF [--name DEPLOY_REF] --containerfile F --context DIR
      After a successful build: generate a CycloneDX 1.5 SBOM FROM THE BUILT
      IMAGE (packages read inside it, offline), then write a receipt binding
      image id <- Containerfile, dependency locks, base-image digests, apt
      snapshot <- source commit + signed-manifest digest. Exit 1 = nothing
      recorded (callers treat it like a failed build).
  sbom    --image REF                 print the SBOM only
  check-deploy --image REF            the deploy gate: does REF's image id carry a
      receipt? FAILED when the name has receipts but this id has none (a
      substituted tag); UNVERIFIED when the name never had one (pre-provenance).
  record-external [--running] [--image REF ...]
      SBOM + identity for third-party images we do not build (informational).
  verify  [--running] [--image REF ...] [--deep] [--json]
      Per image: receipt present / id match / SBOM digest / (deep) SBOM re-derived
      from the artifact / source commit signed and its MANIFEST digest / build
      inputs at that commit / current or older source.

Statuses: VERIFIED, FAILED, UNVERIFIED, NOT APPLICABLE. Exit 1 if any check
FAILED; UNVERIFIED never exits non-zero on its own (it is reported, not hidden).

Storage (deployment profile, not product policy):
  BUILD_PROVENANCE_DIR  default ${XDG_STATE_HOME:-~/.local/state}/build-provenance
    <image-id>/receipt.json, <image-id>/sbom.cdx.json, by-name/<name>.json, ledger.jsonl
Receipts are not signed; their trust comes from being bound to content-
addressed image ids and to a signed source commit (see the doc's limits).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buildlib  # noqa: E402

SCHEMA = 1
STORE = Path(os.environ.get("BUILD_PROVENANCE_DIR")
             or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "build-provenance")
DEP_FILE_RE = re.compile(r"(^|/)(requirements[^/]*\.txt|package-lock\.json|package\.json)$")

COLLECTOR = r"""
echo "@os"; grep -E '^(ID|VERSION_ID)=' /etc/os-release 2>/dev/null
if command -v dpkg-query >/dev/null 2>&1; then echo "@deb"; dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n'; fi
if command -v apk >/dev/null 2>&1; then echo "@apk"; apk info -v 2>/dev/null; fi
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    echo "@pypi"
    "$py" - <<'PY'
import importlib.metadata as m
seen = set()
for d in m.distributions():
    n = d.metadata["Name"]
    if n and (n.lower(), d.version) not in seen:
        seen.add((n.lower(), d.version)); print(f"{n}\t{d.version}")
PY
    break
  fi
done
"""


def run(cmd, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("text", True)
    return subprocess.run(cmd, capture_output=True, **kw)


def inspect_image(ref: str) -> dict | None:
    r = run(["podman", "image", "inspect", ref])
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)[0]


# --------------------------------------------------------------------- SBOM

def collect_packages(ref: str) -> tuple[dict, list[dict]]:
    r = run(["podman", "run", "--rm", "-i", "--network", "none", "--entrypoint", "sh", ref, "-s"],
            input=COLLECTOR, timeout=300)
    if r.returncode != 0 and not r.stdout:
        raise RuntimeError(f"cannot read packages inside {ref}: {r.stderr.strip()[-300:]}")
    osinfo, comps, section = {}, [], None
    for ln in r.stdout.splitlines():
        if ln.startswith("@"):
            section = ln[1:]
            continue
        if section == "os" and "=" in ln:
            k, v = ln.split("=", 1)
            osinfo[k] = v.strip('"')
        elif section == "deb" and ln.count("\t") == 2:
            n, v, a = ln.split("\t")
            distro = f"{osinfo.get('ID', 'debian')}-{osinfo.get('VERSION_ID', '')}".rstrip("-")
            comps.append({"type": "library", "name": n, "version": v,
                          "purl": f"pkg:deb/{osinfo.get('ID', 'debian')}/{n}@{quote(v, safe='')}?arch={a}&distro={distro}"})
        elif section == "apk" and ln.strip():
            m = re.match(r"^(.+)-([^-]+-r\d+)$", ln.strip())
            if m:
                comps.append({"type": "library", "name": m.group(1), "version": m.group(2),
                              "purl": f"pkg:apk/{osinfo.get('ID', 'alpine')}/{m.group(1)}@{quote(m.group(2), safe='')}"})
        elif section == "pypi" and "\t" in ln:
            n, v = ln.split("\t", 1)
            norm = re.sub(r"[-_.]+", "-", n).lower()
            comps.append({"type": "library", "name": norm, "version": v, "purl": f"pkg:pypi/{norm}@{quote(v, safe='')}"})
    comps.sort(key=lambda c: c["purl"])
    return osinfo, comps


def make_sbom(ref: str, info: dict) -> dict:
    osinfo, comps = collect_packages(ref)
    image_id = info["Id"]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        # deterministic: same image -> same serial; no generation timestamp inside
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'oci-image-id:' + image_id)}",
        "version": 1,
        "metadata": {
            "component": {"type": "container", "name": ref.split("@")[0].rsplit(":", 1)[0],
                          "version": "sha256:" + image_id,
                          "properties": [{"name": "oci:architecture", "value": info.get("Architecture", "")},
                                         {"name": "oci:os", "value": info.get("Os", "")},
                                         {"name": "os-release", "value": f"{osinfo.get('ID', '')} {osinfo.get('VERSION_ID', '')}".strip()}]},
            "tools": {"components": [{"type": "application", "name": "provenance.py",
                                      "description": "packages read inside the built image"}]},
        },
        "components": comps,
    }


# ---------------------------------------------------------------- source facts

def git(repo: Path, *args) -> str:
    r = run(["git", "-C", str(repo), *args])
    return r.stdout.strip() if r.returncode == 0 else ""


def source_facts(context: Path) -> dict:
    top = Path(git(context, "rev-parse", "--show-toplevel") or context)
    man = top / "MANIFEST.sha256"
    return {
        "repo_root": top,
        "source_commit": git(top, "rev-parse", "HEAD") or None,
        "source_tree_status": "unknown" if not git(top, "rev-parse", "HEAD") else
                              ("clean" if not git(top, "status", "--porcelain") else "dirty"),
        "source_manifest_digest": buildlib.sha256_file(man) if man.is_file() else None,
        "source_manifest_signature_digest": buildlib.sha256_file(top / "MANIFEST.sha256.asc")
                                             if (top / "MANIFEST.sha256.asc").is_file() else None,
    }


def build_inputs(containerfile: Path, context: Path, repo_root: Path) -> dict:
    text = containerfile.read_text()
    froms = buildlib.parse_from_lines(text)
    deps = []
    for _, ln in buildlib.logical_lines(text):
        m = re.match(r"^\s*COPY\s+(?!--from)(?:--\S+\s+)*(.+)$", ln, re.I)
        if not m:
            continue
        for src in m.group(1).split()[:-1]:
            p = (context / src)
            for f in sorted(p.parent.glob(p.name)) if any(c in src for c in "*?") else [p]:
                if f.is_file() and DEP_FILE_RE.search(str(f)):
                    deps.append({"path": str(f.resolve().relative_to(repo_root.resolve())), "digest": buildlib.sha256_file(f)})
    return {
        "containerfile": {"path": str(containerfile.resolve().relative_to(repo_root.resolve())),
                          "digest": buildlib.sha256_file(containerfile)},
        "context": str(context.resolve().relative_to(repo_root.resolve())) or ".",
        "dependency_inputs": deps,
        "base_images": [{"ref": f.ref, "name": f.name, "tag": f.tag, "digest": f.digest} for f in froms],
        "apt_snapshot": buildlib.containerfile_args(text).get("APT_SNAPSHOT"),
    }


def tool_versions() -> dict:
    v = {}
    r = run(["podman", "version", "--format", "{{.Client.Version}}"])
    v["podman"] = r.stdout.strip() if r.returncode == 0 else None
    return v


def name_key(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


# ------------------------------------------------------------------- record

def host_arch() -> str:
    """The architecture images must be built for on this host, in OCI terms.
    Deployment profile: BUILD_EXPECTED_ARCH overrides (e.g. a cross-build host)."""
    import platform
    m = platform.machine().lower()
    return os.environ.get("BUILD_EXPECTED_ARCH") or {"aarch64": "arm64", "x86_64": "amd64"}.get(m, m)


def record(ref: str, name: str | None, containerfile: Path, context: Path) -> int:
    info = inspect_image(ref)
    if not info:
        print(f"provenance: image {ref} not found", file=sys.stderr)
        return 1
    # 2026-10-08 (security review 06): a cached foreign-architecture base image
    # (left by an amd64 portability test) made four production builds amd64; they
    # ran under emulation and could not load their Postgres driver. An image for
    # the wrong architecture is never recorded, so it is held like a failed build.
    if info.get("Architecture") != host_arch():
        print(f"provenance: {ref} is {info.get('Architecture')}, this host builds {host_arch()} -- refusing "
              f"(a cached foreign-architecture base image?)", file=sys.stderr)
        return 1
    try:
        sbom = make_sbom(ref, info)
    except Exception as e:  # noqa: BLE001 -- any failure = nothing recorded
        print(f"provenance: SBOM failed for {ref}: {e}", file=sys.stderr)
        return 1
    if not sbom["components"]:
        print(f"provenance: SBOM for {ref} is empty -- refusing to record", file=sys.stderr)
        return 1
    src = source_facts(context)
    image_id = info["Id"]
    d = STORE / image_id
    d.mkdir(parents=True, exist_ok=True)
    sbom_bytes = buildlib.canonical_json(sbom)
    (d / "sbom.cdx.json").write_bytes(sbom_bytes)
    receipt = {
        "schema_version": SCHEMA,
        "project": os.environ.get("BUILD_PROJECT") or src["repo_root"].name,
        # local path of the source checkout: deployment-private, never published
        "source_repo": str(src["repo_root"]),
        "source_commit": src["source_commit"],
        "source_tree_status": src["source_tree_status"],
        "source_manifest_digest": src["source_manifest_digest"],
        "source_manifest_signature_digest": src["source_manifest_signature_digest"],
        "build_policy_digest": buildlib.sha256_file(src["repo_root"] / buildlib.POLICY_REL)
                               if (src["repo_root"] / buildlib.POLICY_REL).is_file() else None,
        "build_timestamp": info.get("Created"),
        "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH"),
        "builder": tool_versions() | {"buildah_label": (info.get("Labels") or {}).get("io.buildah.version")},
        "architecture": info.get("Architecture"),
        "inputs": build_inputs(containerfile, context, src["repo_root"]),
        "image": {"built_as": ref, "deploy_name": name or ref, "id": "sha256:" + image_id,
                  "manifest_digest": info.get("Digest")},
        "sbom": {"format": "CycloneDX 1.5 JSON", "digest": buildlib.sha256_bytes(sbom_bytes),
                 "components": len(sbom["components"])},
        "recorded_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    rb = buildlib.canonical_json(receipt)
    (d / "receipt.json").write_bytes(rb)
    (STORE / "by-name").mkdir(exist_ok=True)
    (STORE / "by-name" / f"{name_key(name or ref)}.json").write_bytes(
        buildlib.canonical_json({"name": name or ref, "id": "sha256:" + image_id, "receipt_digest": buildlib.sha256_bytes(rb)}))
    with open(STORE / "ledger.jsonl", "a") as f:
        f.write(json.dumps({"id": "sha256:" + image_id, "name": name or ref, "receipt_digest": buildlib.sha256_bytes(rb),
                            "at": receipt["recorded_at"]}, sort_keys=True) + "\n")
    print(f"provenance: recorded {name or ref} {image_id[:12]} ({len(sbom['components'])} components, "
          f"source {str(src['source_commit'])[:7]} {src['source_tree_status']})")
    return 0


# -------------------------------------------------------------------- verify

V, F, U, NA = "VERIFIED", "FAILED", "UNVERIFIED", "NOT APPLICABLE"


def load_receipt(image_id: str) -> tuple[dict | None, Path]:
    d = STORE / image_id.removeprefix("sha256:")
    p = d / "receipt.json"
    return (json.loads(p.read_text()) if p.is_file() else None), d


def known_names() -> dict[str, dict]:
    out = {}
    for p in (STORE / "by-name").glob("*.json") if (STORE / "by-name").is_dir() else []:
        j = json.loads(p.read_text())
        out[j["name"]] = j
    return out


def verify_image(ref: str, deep: bool, names: dict | None = None,
                 name_hint: str | None = None) -> list[tuple[str, str, str]]:
    names = known_names() if names is None else names
    res: list[tuple[str, str, str]] = []
    info = inspect_image(ref)
    if not info:
        return [(F, "image", f"{ref} not found")]
    image_id = info["Id"]
    receipt, d = load_receipt(image_id)
    if receipt is None:
        name = name_hint or ref                   # for an id, the image name the container was started from
        if name in names:
            return [(F, "receipt", f"{name} -> {image_id[:12]} has NO receipt, but {name} was last recorded as "
                                    f"{names[name]['id'][7:19]}: an image this host did not record building is in use")]
        return [(U, "receipt", f"{name} -> {image_id[:12]}: no provenance recorded (built before provenance, or not by the rollout tooling)")]
    res.append((V if receipt["image"]["id"] == "sha256:" + image_id else F, "image id",
                f"receipt names {receipt['image']['id'][7:19]}, image is {image_id[:12]}"))
    sb = d / "sbom.cdx.json"
    if not sb.is_file():
        res.append((F, "sbom", "SBOM file missing"))
    else:
        dig = buildlib.sha256_file(sb)
        res.append((V if dig == receipt["sbom"]["digest"] else F, "sbom digest",
                    "matches the receipt" if dig == receipt["sbom"]["digest"] else "SBOM file differs from the digest in the receipt"))
        if deep:
            try:
                fresh = buildlib.sha256_bytes(buildlib.canonical_json(make_sbom(ref, info)))
                res.append((V if fresh == receipt["sbom"]["digest"] else F, "sbom vs artifact",
                            "re-derived from the image, identical" if fresh == receipt["sbom"]["digest"]
                            else "packages inside the image differ from the recorded SBOM"))
            except Exception as e:  # noqa: BLE001
                res.append((U, "sbom vs artifact", f"could not re-derive: {e}"))
    if receipt.get("kind") == "external":
        res.append((U, "build provenance", "third-party image: identity and contents recorded; "
                                           "built elsewhere from a floating tag, so its inputs are not pinned here"))
        return res
    res += verify_source(receipt)
    return res


def verify_source(receipt: dict) -> list[tuple[str, str, str]]:
    out = []
    commit = receipt.get("source_commit")
    # the repository the image was built from (recorded), else this one
    repo = Path(receipt.get("source_repo") or buildlib.repo_root())
    if not commit or not repo.is_dir() or \
            run(["git", "-C", str(repo), "cat-file", "-e", commit + "^{commit}"]).returncode != 0:
        return [(U, "source", f"commit {str(commit)[:7]} not found in {repo.name}")]
    if receipt.get("source_tree_status") != "clean":
        out.append((F, "source tree", f"built from a {receipt.get('source_tree_status')} tree, not a committed one"))
    sig = run(["git", "-C", str(repo), "log", "-1", "--format=%G?", commit]).stdout.strip()
    out.append((V if sig in ("G", "U") else (U if sig in ("E", "") else F), "commit signature",
                f"git signature status {sig or '?'} on {commit[:7]}"))
    man = run(["git", "-C", str(repo), "show", f"{commit}:MANIFEST.sha256"], text=False)
    if man.returncode == 0:
        ok = buildlib.sha256_bytes(man.stdout) == receipt.get("source_manifest_digest")
        out.append((V if ok else F, "signed manifest", "receipt's manifest digest matches the commit" if ok
                    else "receipt's manifest digest does NOT match the commit's MANIFEST.sha256"))
    else:
        out.append((U, "signed manifest", "no MANIFEST.sha256 at that commit"))
    inp = receipt.get("inputs", {})
    for item in [inp.get("containerfile")] + list(inp.get("dependency_inputs", [])):
        if not item:
            continue
        blob = run(["git", "-C", str(repo), "show", f"{commit}:{item['path']}"], text=False)
        if blob.returncode != 0:
            out.append((F, "build input", f"{item['path']} not in commit {commit[:7]}"))
        elif buildlib.sha256_bytes(blob.stdout) != item["digest"]:
            out.append((F, "build input", f"{item['path']} differs from commit {commit[:7]} (built from a modified file)"))
    if not any(s == F and n == "build input" for s, n, _ in out):
        out.append((V, "build inputs", f"Containerfile + {len(inp.get('dependency_inputs', []))} dependency file(s) match commit {commit[:7]}"))
    bases = inp.get("base_images", [])
    out.append((V if bases and all(b.get("digest") for b in bases) else F, "base images",
                ", ".join(f"{b['name']}@{(b.get('digest') or 'UNPINNED')[:19]}" for b in bases) or "none recorded"))
    head = git(repo, "rev-parse", "HEAD")
    if commit == head:
        out.append((V, "current source", "built from the current HEAD"))
    else:
        behind = git(repo, "rev-list", "--count", f"{commit}..{head}")
        out.append((U, "current source", f"built from {commit[:7]}, {behind or '?'} commit(s) behind HEAD {head[:7]}"))
    return out


def local_containers() -> list[tuple[str, str, str, bool]]:
    """(container name, image name, ACTUAL image id, running?) for every container,
    exited ones included -- timer oneshots are mostly exited when the sweep runs.
    Final-pass finding F1 (review 05): checking only the tag of running containers
    missed both a container whose tag moved after it started and oneshots that
    start from a substituted tag between sweeps."""
    r = run(["podman", "ps", "-a", "--no-trunc", "--format", "{{.Names}}\t{{.Image}}\t{{.ImageID}}\t{{.State}}"])
    out = []
    for ln in r.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) == 4 and parts[1].startswith("localhost/"):
            out.append((parts[0], parts[1], parts[2].removeprefix("sha256:"), parts[3] == "running"))
    return out


def record_external(running: bool, refs: list[str]) -> int:
    """SBOM + a minimal receipt for images we do NOT build (kind=external).
    Records identity (image id, registry digest, tag) and contents; it does not
    make a floating tag reproducible and verify() always reports that."""
    targets = list(refs)
    if running:
        r = run(["podman", "ps", "--format", "{{.Image}}"])
        targets += sorted({i for i in r.stdout.split() if not i.startswith("localhost/")})
    bad = 0
    for ref in targets:
        info = inspect_image(ref)
        if not info:
            bad += 1
            continue
        d = STORE / info["Id"]
        if (d / "receipt.json").is_file():
            continue
        try:
            sbom = make_sbom(ref, info)
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        d.mkdir(parents=True, exist_ok=True)
        sb = buildlib.canonical_json(sbom)
        (d / "sbom.cdx.json").write_bytes(sb)
        (d / "receipt.json").write_bytes(buildlib.canonical_json({
            "schema_version": SCHEMA, "kind": "external",
            "image": {"deploy_name": ref, "id": "sha256:" + info["Id"],
                      "repo_digests": sorted(info.get("RepoDigests") or [])},
            "architecture": info.get("Architecture"),
            "sbom": {"format": "CycloneDX 1.5 JSON", "digest": buildlib.sha256_bytes(sb), "components": len(sbom["components"])},
            "recorded_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
    return 1 if bad else 0


def check_deploy(ref: str) -> int:
    res = verify_image(ref, deep=False)
    for s, n, msg in res:
        if s in (F, U) and n in ("receipt", "image id", "sbom digest", "image"):
            print(f"{s:10} {ref}: {msg}")
            return 1 if s == F else 0
    print(f"VERIFIED   {ref}: receipt present for this image id")
    return 0


def verify(refs: list[str], running: bool, deep: bool, as_json: bool) -> int:
    targets = [(r, r, None) for r in refs]
    if running:
        tags, ids = {}, {}
        for cname, img, iid, is_running in local_containers():
            tags.setdefault(img, cname)                       # what the NEXT start of any unit would use
            if is_running:
                ids.setdefault((img, iid), cname)             # what is running NOW
        for img, cname in sorted(tags.items()):
            targets.append((img, f"{img} [tag; next start of e.g. {cname}]", img))
        for (img, iid), cname in sorted(ids.items()):
            targets.append(("sha256:" + iid, f"{img} [running {iid[:12]} in e.g. {cname}]", img))
    names = known_names()
    report, failed = [], False
    for ref, label, *hint in targets:
        rows = verify_image(ref, deep, names, hint[0] if hint else None)
        failed |= any(s == F for s, _, _ in rows)
        report.append({"image": label, "checks": [{"status": s, "check": n, "detail": m} for s, n, m in rows]})
    if as_json:
        print(json.dumps(report, indent=1))
    else:
        for r in report:
            worst = F if any(c["status"] == F for c in r["checks"]) else (
                U if any(c["status"] == U for c in r["checks"]) else V)
            print(f"{worst:10} {r['image']}")
            for c in r["checks"]:
                print(f"    {c['status']:14} {c['check']:18} {c['detail']}")
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record"); r.add_argument("--image", required=True); r.add_argument("--name")
    r.add_argument("--containerfile", required=True, type=Path); r.add_argument("--context", required=True, type=Path)
    s = sub.add_parser("sbom"); s.add_argument("--image", required=True)
    c = sub.add_parser("check-deploy"); c.add_argument("--image", required=True)
    x = sub.add_parser("record-external"); x.add_argument("--running", action="store_true")
    x.add_argument("--image", action="append", default=[])
    v = sub.add_parser("verify"); v.add_argument("--image", action="append", default=[])
    v.add_argument("--running", action="store_true"); v.add_argument("--deep", action="store_true")
    v.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "record":
        cf = a.containerfile if a.containerfile.is_absolute() else (a.context / a.containerfile)
        if not cf.is_file():
            cf = a.containerfile
        return record(a.image, a.name, cf, a.context)
    if a.cmd == "sbom":
        info = inspect_image(a.image)
        if not info:
            return 1
        sys.stdout.buffer.write(buildlib.canonical_json(make_sbom(a.image, info)))
        return 0
    if a.cmd == "check-deploy":
        return check_deploy(a.image)
    if a.cmd == "record-external":
        return record_external(a.running, a.image)
    return verify(a.image, a.running, a.deep, a.json)


if __name__ == "__main__":
    sys.exit(main())
