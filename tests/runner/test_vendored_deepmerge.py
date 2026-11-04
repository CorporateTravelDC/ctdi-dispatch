"""tests/runner/test_vendored_deepmerge.py -- the runner frontend's build uses a vendored deepmerge
4.3.1 with GHSA-gcqx-w48m-w2h4 closed (2026-11-04). Node runs the proof when it is installed."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FE = Path(__file__).resolve().parents[2] / "src" / "runner" / "frontend"


def test_package_json_pins_the_vendored_copy_and_the_lockfile_links_it():
    pkg = json.loads((FE / "package.json").read_text())
    assert pkg["devDependencies"]["deepmerge"] == "file:./vendor/deepmerge"
    assert pkg["overrides"]["deepmerge"] == "$deepmerge"
    lock = json.loads((FE / "package-lock.json").read_text())["packages"]
    assert lock["node_modules/deepmerge"] == {"resolved": "vendor/deepmerge", "link": True}
    assert not any(k.endswith("/deepmerge") and "registry.npmjs.org" in str(v.get("resolved", ""))
                   for k, v in lock.items())


def test_containerfile_copies_vendor_before_npm_ci():
    cf = (FE.parents[2] / "Containerfile.runner").read_text()
    assert cf.index("COPY src/runner/frontend/vendor/ ./vendor/") < cf.index("RUN npm ci")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_vendored_copy_blocks_prototype_poisoning_and_still_merges():
    js = f"""
const dm = require({json.dumps(str(FE / 'vendor' / 'deepmerge'))});
const r = dm(JSON.parse('{{"__proto__": {{"p": 1}}, "a": 1}}'), JSON.parse('{{"__proto__": {{"q": 1}}, "constructor": {{"x": 1}}, "b": {{"c": 2}}}}'));
const ok = Object.getPrototypeOf(r) === Object.prototype && r.p === undefined && r.q === undefined
  && !Object.prototype.hasOwnProperty.call(r, 'constructor') && r.a === 1 && r.b.c === 2
  && JSON.stringify(dm({{a: {{x: 1}}, l: [1]}}, {{a: {{y: 2}}, l: [2]}})) === '{{"a":{{"x":1,"y":2}},"l":[1,2]}}'
  && JSON.stringify(dm.all([{{a: 1}}, {{b: 2}}, {{a: 3}}])) === '{{"a":3,"b":2}}';
process.exit(ok ? 0 : 1);
"""
    assert subprocess.run(["node", "-"], input=js, text=True).returncode == 0
