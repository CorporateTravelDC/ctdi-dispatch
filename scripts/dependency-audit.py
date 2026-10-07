#!/usr/bin/env python3
"""scripts/dependency-audit.py -- known-vulnerability audit for every repo and
every running first-party image (2026-10-07).

Why: GitHub Dependabot was enabled only on the public platform mirror, so the
private and site repos were never scanned, and Dependabot reads manifests, not
what is actually installed. The 2026-10-07 pass found 19 of 24 advisories this
way (docs/security-reviews/2026-10-07-03-post-deploy-and-dependencies.md).
Dependabot alerts are now on for every repo as a second layer; THIS is the
primary check, and it runs before a push.

Modes
  --repo PATH   audit one repo's manifests (the pre-push hook)
  --images      audit what is installed in each running first-party container AND
                in every other local image tag (rollback :previous, test and debug
                tags, images of disabled units) -- "dormant" images
  --all         every repo in scripts/lib/dependency-audit-repos.txt, each repo's
                git worktrees, + --images
                (the daily timer; pushes a summary to ntfy on findings)

Standing rule (operator, 2026-10-07): disabled, dormant or not deployed is NOT a
reason to leave a known-vulnerable dependency in place. Dormant images and
worktrees are audited and block exactly like running ones, so re-enabling a
unit or rolling back can never bring a known vulnerability back silently.
  --setup       create the private pip-audit venv

What is audited
  package-lock.json  -> `npm audit --package-lock-only` (offline-safe: lockfile only)
  requirements*.txt  -> `pip-audit -r` (resolves the file as pip would)
  running images     -> `pip freeze` inside the container -> `pip-audit --no-deps`

Policy (exit codes): 0 clean / only low-moderate npm advisories;
  1 a BLOCKING finding: npm high/critical, or any Python advisory with a fix.
  3 the audit could not run (tool missing, registry unreachable) -- a pre-push
    warns and lets the push through; the daily run reports it.
The pre-push override is DEPENDENCY_AUDIT_OVERRIDE=1 (operator only; logged).
Never edits a repo or an image -- it only reports.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_LIST = HERE / "lib" / "dependency-audit-repos.txt"
VENV = Path(os.environ.get("DEPENDENCY_AUDIT_VENV", Path.home() / ".local/share/ctdc-dependency-audit/venv"))
PIP_AUDIT = VENV / "bin" / "pip-audit"
CACHE = Path.home() / ".cache" / "ctdc-dependency-audit"
STATE = Path(os.environ.get("DEPENDENCY_AUDIT_STATE", Path.home() / ".cache/ctdc-dependency-audit/last-run.json"))
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "__pycache__", "dist", "build", ".wrangler"}
FIRST_PARTY_PREFIXES = ("corporatetraveldc-", "systemd-corporatetraveldc-", "csexec-", "systemd-amtrak-tracker")
BLOCKING_NPM = ("high", "critical")


def manifests(repo: Path) -> list[Path]:
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f == "package-lock.json" or (f.startswith("requirements") and f.endswith(".txt")):
                out.append(Path(root) / f)
    return sorted(out)


def npm_audit(lockfile: Path) -> dict:
    r = subprocess.run(["npm", "audit", "--package-lock-only", "--json"], cwd=lockfile.parent,
                       capture_output=True, text=True, timeout=300)
    try:
        meta = json.loads(r.stdout or "{}").get("metadata", {}).get("vulnerabilities", {})
    except ValueError:
        return {"error": (r.stderr or "unparseable npm output").strip()[:200]}
    counts = {k: int(meta.get(k, 0)) for k in ("low", "moderate", "high", "critical")}
    return {"counts": counts, "blocking": sum(counts[k] for k in BLOCKING_NPM)}


def pip_audit(req: Path, no_deps: bool = False) -> dict:
    if not PIP_AUDIT.exists():
        return {"error": f"pip-audit not installed ({PIP_AUDIT}); run --setup"}
    cmd = [str(PIP_AUDIT), "-r", str(req), "-f", "json", "--progress-spinner", "off", "--cache-dir", str(CACHE)]
    if no_deps:
        cmd += ["--no-deps", "--disable-pip"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        return {"error": (r.stderr or "unparseable pip-audit output").strip().splitlines()[-1][:200]
                if (r.stderr or "").strip() else "pip-audit failed"}
    vulns = {}
    for dep in data.get("dependencies", []):
        for v in dep.get("vulns", []):
            vulns[(dep["name"], dep.get("version"), v["id"])] = ",".join(v.get("fix_versions") or [])
    findings = [{"package": n, "version": ver, "id": i, "fix": fx} for (n, ver, i), fx in sorted(vulns.items())]
    return {"findings": findings, "blocking": sum(1 for f in findings if f["fix"])}


def audit_repo(repo: Path) -> list[dict]:
    res = []
    for m in manifests(repo):
        r = npm_audit(m) if m.name == "package-lock.json" else pip_audit(m)
        res.append({"target": str(m.relative_to(repo)), "repo": repo.name, **r})
    return res


def worktrees(repo: Path) -> list[Path]:
    """Other checkouts of the same repo (feature branches); the main one excluded."""
    r = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"], capture_output=True, text=True)
    paths = [Path(l[9:]) for l in r.stdout.splitlines() if l.startswith("worktree ")]
    return [p for p in paths if p.resolve() != repo.resolve() and p.is_dir()]


def _audit_freeze(target: str, freeze: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write("\n".join(l for l in freeze.splitlines() if "==" in l and not l.startswith("-e")))
    try:
        return {"target": target, "repo": "(images)", **pip_audit(Path(fh.name), no_deps=True)}
    finally:
        os.unlink(fh.name)


def audit_dormant_images(running_ids: set[str]) -> list[dict]:
    """Every localhost/* tag not backing a running container: rollback targets,
    test/debug tags, images of disabled units. Runs pip freeze offline."""
    out = subprocess.run(["podman", "images", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"],
                         capture_output=True, text=True).stdout
    res = []
    for ln in sorted(out.splitlines()):
        tag, _, iid = ln.partition(" ")
        if not tag.startswith("localhost/") or "<none>" in tag or iid.split(":")[-1] in running_ids:
            continue
        fr = subprocess.run(["podman", "run", "--rm", "--network", "none", "--entrypoint", "pip", tag, "freeze"],
                            capture_output=True, text=True, timeout=120)
        if fr.returncode == 0 and fr.stdout.strip():
            res.append(_audit_freeze(f"image:{tag} (dormant)", fr.stdout))
    return res


def audit_images() -> list[dict]:
    names = subprocess.run(["podman", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    running_ids = {i.split(":")[-1] for i in subprocess.run(
        ["podman", "ps", "--no-trunc", "--format", "{{.ImageID}}"], capture_output=True, text=True).stdout.split()}
    res = []
    for n in sorted(names):
        if not n.startswith(FIRST_PARTY_PREFIXES):
            continue
        fr = subprocess.run(["podman", "exec", n, "pip", "freeze"], capture_output=True, text=True, timeout=60)
        if fr.returncode != 0 or not fr.stdout.strip():
            continue
        res.append(_audit_freeze(f"image:{n}", fr.stdout))
    return res + audit_dormant_images(running_ids)


def summarize(results: list[dict]) -> tuple[int, list[str]]:
    lines, blocking, errors = [], 0, 0
    for r in results:
        if "error" in r:
            errors += 1
            lines.append(f"  ?  {r['repo']}:{r['target']}  audit unavailable: {r['error']}")
            continue
        blocking += r.get("blocking", 0)
        if "counts" in r:
            c = r["counts"]
            mark = "XX" if r["blocking"] else ("!" if c["moderate"] or c["low"] else "ok")
            lines.append(f"  {mark:>2} {r['repo']}:{r['target']}  npm high+critical={r['blocking']} moderate={c['moderate']} low={c['low']}")
        else:
            mark = "XX" if r["blocking"] else ("!" if r["findings"] else "ok")
            lines.append(f"  {mark:>2} {r['repo']}:{r['target']}  python advisories={len(r['findings'])} (fixable {r['blocking']})")
            for f in r["findings"][:8]:
                lines.append(f"       {f['package']} {f['version']} {f['id']} -> {f['fix'] or 'no fix yet'}")
    rc = 1 if blocking else (3 if errors and not any("error" not in r for r in results) else 0)
    return rc, lines


def ntfy(title: str, body: str, priority: int) -> None:
    env = {}
    for path in ("/etc/corporatetraveldc/dispatch.env", "/etc/corporatetraveldc/dispatch-secrets.env"):
        try:
            for ln in open(path, encoding="utf-8"):
                if "=" in ln and not ln.lstrip().startswith("#"):
                    k, v = ln.rstrip("\n").split("=", 1)
                    env.setdefault(k, v.strip().strip("'\""))
        except OSError:
            pass
    base = env.get("NTFY_BASE_URL") or "http://127.0.0.1:2586"
    topic = env.get("NTFY_OPS_TOPIC") or "ops-health"
    hdr = []
    if env.get("NTFY_TOKEN"):
        # token on a private fd via curl's header file, never on argv
        r, w = os.pipe()
        os.write(w, f"Authorization: Bearer {env['NTFY_TOKEN']}\n".encode()); os.close(w)
        hdr = ["-H", f"@/dev/fd/{r}"]
    subprocess.run(["curl", "-sf", "--max-time", "8", *hdr, "-H", f"Title: {title}", "-H", f"Priority: {priority}",
                    "-H", "Tags: package", "--data-binary", "@-", f"{base}/{topic}"],
                   input=body.encode(), capture_output=True, pass_fds=([int(hdr[1].rsplit('/', 1)[1])] if hdr else []))


def setup() -> int:
    VENV.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    subprocess.run([str(VENV / "bin" / "pip"), "install", "-q", "--upgrade", "pip-audit"], check=True)
    print(f"pip-audit installed in {VENV}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--repo", type=Path)
    g.add_argument("--images", action="store_true")
    g.add_argument("--all", action="store_true")
    g.add_argument("--setup", action="store_true")
    a = ap.parse_args(argv)
    if a.setup:
        return setup()
    results = []
    if a.repo:
        results = audit_repo(a.repo.resolve())
    if a.all:
        for ln in REPO_LIST.read_text().splitlines():
            ln = ln.split("#", 1)[0].strip()
            if ln and Path(ln).is_dir():
                results += audit_repo(Path(ln))
                for wt in worktrees(Path(ln)):
                    results += audit_repo(wt)
    if a.images or a.all:
        results += audit_images()
    rc, lines = summarize(results)
    print(f"dependency-audit: {len(results)} target(s) -- " + {0: "clean", 1: "BLOCKING findings", 3: "audit unavailable"}[rc])
    print("\n".join(lines))
    if a.all:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"at": time.time(), "rc": rc, "lines": lines}, indent=1))
        if rc:
            ntfy("Dependency audit: " + ("vulnerable packages" if rc == 1 else "could not run"),
                 "\n".join(l for l in lines if not l.strip().startswith("ok"))[:3500], 4 if rc == 1 else 3)
    return rc


if __name__ == "__main__":
    sys.exit(main())
