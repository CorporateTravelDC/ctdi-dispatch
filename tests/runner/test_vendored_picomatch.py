"""tests/runner/test_vendored_picomatch.py -- the runner frontend's build uses a vendored picomatch 4.0.7
with GHSA-fc5q-39xc-vjpf (CVE-2026-112366) closed (2026-11-18). Upstream through 4.0.7 (the latest)
backtracks exponentially on single-branch repeated extglobs such as `+(*a)`: about 21 s for a
29-character input on this box. Node runs the proofs when it is installed."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FE = Path(__file__).resolve().parents[2] / "src" / "runner" / "frontend"
VENDOR = FE / "vendor" / "picomatch"


def test_package_json_pins_the_vendored_copy_and_the_lockfile_links_it():
    pkg = json.loads((FE / "package.json").read_text())
    assert pkg["devDependencies"]["picomatch"] == "file:./vendor/picomatch"
    assert pkg["overrides"]["picomatch"] == "$picomatch"
    lock = json.loads((FE / "package-lock.json").read_text())["packages"]
    assert lock["node_modules/picomatch"] == {"resolved": "vendor/picomatch", "link": True}
    assert lock["vendor/picomatch"]["version"] == "4.0.7-ctdc.1"
    assert not any(k.endswith("/picomatch") and "registry.npmjs.org" in str(v.get("resolved", ""))
                   for k, v in lock.items())


def test_vendored_copy_is_complete_and_licensed():
    for rel in ("index.js", "posix.js", "LICENSE", "package.json", "lib/parse.js", "lib/picomatch.js",
                "lib/scan.js", "lib/utils.js", "lib/constants.js"):
        assert (VENDOR / rel).is_file(), rel
    assert "GHSA-fc5q-39xc-vjpf" in (VENDOR / "lib" / "parse.js").read_text()


def test_vendored_dist_directories_are_tracked_not_ignored():
    # 2026-11-04 deepmerge's dist/cjs.js was silently dropped by .gitignore's dist/ rule
    r = subprocess.run(["git", "check-ignore", "-q", str(FE / "vendor" / "deepmerge" / "dist" / "cjs.js")],
                       cwd=FE.parents[2])
    assert r.returncode == 1, "vendor/*/dist/ must not be git-ignored"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_single_branch_repeated_extglobs_finish_fast_and_normal_globs_still_match():
    js = f"""
const pm = require({json.dumps(str(VENDOR))});
const s = 'a'.repeat(40) + '!';
const t0 = Date.now();
for (const p of ['+(*)', '+(*a)', '*(*a)', '+(*|a)', '+(a*)', '*(*b|c)']) pm.isMatch(s, p);
const slow = Date.now() - t0;
const ok = slow < 1000
  && pm.isMatch('src/a.js', '**/*.js') && !pm.isMatch('src/a.css', '**/*.js')
  && pm.isMatch('a.css', '*.{{js,css}}') && pm.isMatch('aaa', '+(a)') && pm.isMatch('ab', '+(a|b)')
  && pm.isMatch('x.js', '!(*.css)') && !pm.isMatch('x.css', '!(*.css)')
  && pm.isMatch('abc', '*(*(a)|b|c)') && pm.isMatch('file.txt', '*.txt') && pm.isMatch('a/b/c', 'a/**')
  && pm.isMatch('index.html', '**/*.{{js,css,html,ico,png,svg}}');
if (!ok) console.error('slow=' + slow);
process.exit(ok ? 0 : 1);
"""
    assert subprocess.run(["node", "-"], input=js, text=True, timeout=60).returncode == 0
