#!/usr/bin/env python3
"""scripts/build/lock-python.py -- generate and check the hash-locked Python
dependency locks (2026-10-07, security review 05; docs/REPRODUCIBLE_BUILDS.md).

Two layers, per lock listed in build/policy.toml:
  <input>.in   human-maintained intent (ranges, comments, reasons)
  <lock>.txt   machine-generated: every transitive package at one exact
               version, with the sha256 of every published file for that
               version (all platforms), plus `# input-digest:` = the hash of
               the .in closure it was generated from.

Generation is a deliberate maintenance step, never part of a build:
  lock-python.py --setup                    tools venv from build/tools/requirements.txt (hashed)
  lock-python.py                            regenerate every lock, keeping current pins where still valid
  lock-python.py --only requirements.txt    one lock
  lock-python.py --upgrade-package fastapi  move one package (and what it needs) forward
  lock-python.py --upgrade                  move everything forward (review the diff!)
  lock-python.py --seed-constraints F       first generation: prefer the versions in F (e.g. a running image's pip freeze)
  lock-python.py --check                    OFFLINE: every lock present, hashed, and generated from the current .in
  lock-python.py --root DIR ...              operate on another repository that has its own build/policy.toml

Resolution uses `uv pip compile --universal`: one lock valid on every
platform (environment markers), so aarch64 and x86_64 hosts install the same
versions. `--no-build` refuses any version that would need a source build.

Exit codes: 0 ok; 1 a lock is stale, missing or malformed (--check), or
generation failed; 2 usage/tooling problem.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buildlib  # noqa: E402

ROOT = buildlib.repo_root()
TOOLS_VENV = Path(os.environ.get("BUILD_TOOLS_VENV", Path.home() / ".local/share/build-tools/venv"))
UV = TOOLS_VENV / "bin" / "uv"
TOOLS_LOCK = "build/tools/requirements.txt"


def locks(policy: dict, only: list[str] | None) -> list[dict]:
    out = policy.get("python", {}).get("lock", [])
    if only:
        out = [l for l in out if l["lock"] in only or l["input"] in only]
        if not out:
            raise SystemExit(f"no lock in build/policy.toml matches {only}")
    return out


def check(policy: dict, only: list[str] | None) -> int:
    bad = 0
    for l in locks(policy, only):
        lock = ROOT / l["lock"]
        if not lock.is_file():
            print(f"FAILED   {l['lock']}: missing (run lock-python.py)")
            bad += 1
            continue
        text = lock.read_text()
        rec, cur = buildlib.recorded_input_digest(text), buildlib.input_digest(ROOT, l["input"])
        entries, problems = buildlib.parse_lock(text)
        if rec != cur:
            print(f"FAILED   {l['lock']}: STALE -- {l['input']} (or a file it includes) changed since the lock was generated")
            bad += 1
        elif problems:
            print(f"FAILED   {l['lock']}: " + "; ".join(problems[:5]))
            bad += 1
        elif not entries:
            print(f"FAILED   {l['lock']}: no requirements")
            bad += 1
        else:
            print(f"VERIFIED {l['lock']}: {len(entries)} packages, all hashed, current with {l['input']}")
    return 1 if bad else 0


def setup() -> int:
    """Create the private tools venv from the hashed tools lock."""
    TOOLS_VENV.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(TOOLS_VENV)], check=True)
    subprocess.run([str(TOOLS_VENV / "bin" / "pip"), "install", "-q", "--require-hashes", "--no-deps",
                    "--only-binary=:all:", "-r", str(ROOT / TOOLS_LOCK)], check=True)
    print(f"tools installed in {TOOLS_VENV} from {TOOLS_LOCK}")
    return 0


def generate(policy: dict, only, upgrade: bool, upgrade_packages: list[str], seed: Path | None) -> int:
    if not UV.exists():
        print(f"uv not found at {UV}; run: scripts/build/lock-python.py --setup", file=sys.stderr)
        return 2
    rc = 0
    for l in locks(policy, only):
        out = ROOT / l["lock"]
        cmd = [str(UV), "pip", "compile", l["input"], "--universal", "--python-version", l["python"],
               "--generate-hashes", "--no-build", "--quiet", "--output-file", l["lock"],
               "--custom-compile-command", "scripts/build/lock-python.py"]
        if upgrade:
            cmd.append("--upgrade")
        for p in upgrade_packages:
            cmd += ["--upgrade-package", p]
        if seed:
            cmd += ["--constraint", str(seed)]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           env={**os.environ, "UV_NO_CONFIG": "1"})
        if r.returncode != 0:
            print(f"FAILED   {l['lock']}: uv exited {r.returncode}\n{r.stderr.strip()[-1500:]}")
            rc = 1
            continue
        text = out.read_text()
        if seed:
            # the seed file is a one-off input; it must not leak into the header
            text = "\n".join(x for x in text.splitlines() if str(seed) not in x) + "\n"
        header = (f"{buildlib.INPUT_DIGEST_PREFIX}{buildlib.input_digest(ROOT, l['input'])}\n"
                  f"# GENERATED -- do not edit. Intent: {l['input']}; python {l['python']}, universal\n")
        out.write_text(header + text)
        n = len(buildlib.parse_lock(out.read_text())[0])
        print(f"wrote    {l['lock']}: {n} packages")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--only", action="append")
    ap.add_argument("--upgrade", action="store_true")
    ap.add_argument("--upgrade-package", action="append", default=[])
    ap.add_argument("--seed-constraints", type=Path)
    ap.add_argument("--root", type=Path, help="repository root (default: the one containing this script)")
    a = ap.parse_args(argv)
    global ROOT
    if a.root:
        ROOT = buildlib.repo_root(a.root.resolve() / "build" / "policy.toml")
    policy = buildlib.load_policy(ROOT)
    if a.setup:
        return setup()
    if a.check:
        return check(policy, a.only)
    return generate(policy, a.only, a.upgrade, a.upgrade_package, a.seed_constraints)


if __name__ == "__main__":
    sys.exit(main())
