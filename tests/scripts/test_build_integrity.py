"""
2026-10-07, security review 05 -- adversarial tests for the build-integrity
layer (docs/REPRODUCIBLE_BUILDS.md). Each test tries to break one guarantee;
numbers follow the review's list. Offline: no registry, no PyPI, no podman.
Live checks that need the network or real builds (per-architecture builds,
registry digests, SBOM from a real image) are recorded in the review instead.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "scripts" / "build"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def load(name: str):
    sys.path.insert(0, str(BUILD))
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m          # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(m)
    return m


POLICY = """
schema = 1
[base_images]
require_fully_qualified = true
require_digest = true
[os_packages]
apt_snapshot_arg = "APT_SNAPSHOT"
require_single_snapshot = true
[python]
install_flags = ["--no-cache-dir", "--require-hashes", "--no-deps", "--only-binary=:all:"]
[[python.lock]]
input = "requirements.in"
lock = "requirements.txt"
python = "3.13"
consumers = ["Containerfile.app"]
[node]
lockfiles = []
[downloads]
scan = ["Containerfile*", "install/*.sh"]
"""

GOOD_CF = f"""FROM docker.io/library/python:3.13-slim@{DIGEST_A}
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --no-deps --only-binary=:all: -r requirements.txt
ARG APT_SNAPSHOT=20261007T000000Z
RUN printf 'APT::Snapshot "%s";\\n' "${{APT_SNAPSHOT}}" > /etc/apt/apt.conf.d/50snapshot \\
 && apt-get update -qq && apt-get install -y gnupg
"""


class Tree:
    """A minimal throwaway repository that passes the policy; tests then break it."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "build").mkdir()
        (self.root / "build" / "policy.toml").write_text(POLICY)
        (self.root / "requirements.in").write_text("requests>=2.32\n")
        (self.root / "Containerfile.app").write_text(GOOD_CF)
        self.bl = load("buildlib")
        self.write_lock()

    def write_lock(self, extra: str = ""):
        dig = self.bl.input_digest(self.root, "requirements.in")
        (self.root / "requirements.txt").write_text(
            f"# input-digest: {dig}\nrequests==2.34.2 \\\n    --hash=sha256:{'c' * 64}\n{extra}")

    def policy(self):
        bp = load("build-policy")
        return bp.run_all(self.root)

    def statuses(self, check: str) -> list[str]:
        return [s for s, _ in self.policy()[check]]


class BuildPolicyAdversarial(unittest.TestCase):
    def setUp(self):
        self.t = Tree()

    def tearDown(self):
        self.t.tmp.cleanup()

    def test_00_the_baseline_tree_passes(self):
        res = self.t.policy()
        self.assertFalse([d for rows in res.values() for s, d in rows if s == "FAILED"])

    def test_01_direct_requirement_changed_without_refreshing_the_lock_fails(self):
        (self.t.root / "requirements.in").write_text("requests>=2.32\nhttpx>=0.27\n")
        self.assertIn("FAILED", self.t.statuses("locks"))
        self.assertIn("STALE", " ".join(d for _, d in self.t.policy()["locks"]))

    def test_01b_an_included_file_changing_also_makes_the_lock_stale(self):
        (self.t.root / "base.in").write_text("idna>=3\n")
        (self.t.root / "requirements.in").write_text("-r base.in\nrequests>=2.32\n")
        self.t.write_lock()
        self.assertNotIn("FAILED", self.t.statuses("locks"))
        (self.t.root / "base.in").write_text("idna>=3.7\n")
        self.assertIn("FAILED", self.t.statuses("locks"))

    def test_01c_an_unhashed_or_unpinned_line_in_a_lock_fails(self):
        self.t.write_lock(extra="idna==3.20\n")
        self.assertIn("FAILED", self.t.statuses("locks"))
        self.t.write_lock(extra="idna>=3\n")
        self.assertIn("FAILED", self.t.statuses("locks"))
        self.t.write_lock(extra="--index-url https://mirror.invalid/simple\n")
        self.assertIn("FAILED", self.t.statuses("locks"))

    def test_03_replacing_one_pinned_base_digest_is_detected_as_drift(self):
        (self.t.root / "Containerfile.other").write_text(GOOD_CF.replace(DIGEST_A, DIGEST_B))
        details = " ".join(d for s, d in self.t.policy()["base-images"] if s == "FAILED")
        self.assertIn("different digest", details)

    def test_04_a_mutable_base_tag_fails(self):
        (self.t.root / "Containerfile.app").write_text(GOOD_CF.replace(f"@{DIGEST_A}", ""))
        self.assertIn("FAILED", self.t.statuses("base-images"))

    def test_04b_a_short_name_fails_even_with_a_digest(self):
        (self.t.root / "Containerfile.app").write_text(GOOD_CF.replace("docker.io/library/python", "python"))
        self.assertIn("FAILED", self.t.statuses("base-images"))

    def test_04c_an_arg_substituted_base_is_resolved_before_checking(self):
        cf = "ARG BASE=docker.io/library/python:3.13-slim\nFROM ${BASE}\n" + GOOD_CF.split("\n", 1)[1]
        (self.t.root / "Containerfile.app").write_text(cf)
        self.assertIn("FAILED", self.t.statuses("base-images"))

    def test_06_a_new_pip_install_outside_the_lock_fails(self):
        (self.t.root / "Containerfile.app").write_text(GOOD_CF + "RUN pip install requests\n")
        self.assertIn("FAILED", self.t.statuses("python-install"))
        (self.t.root / "Containerfile.app").write_text(GOOD_CF + "RUN python3 -m pip install --upgrade pip\n")
        self.assertIn("FAILED", self.t.statuses("python-install"))

    def test_06b_installing_a_lock_this_containerfile_does_not_own_fails(self):
        (self.t.root / "other.txt").write_text("x")
        cf = GOOD_CF.replace("COPY requirements.txt .", "COPY other.txt requirements.txt")
        (self.t.root / "Containerfile.app").write_text(cf)
        self.assertIn("FAILED", self.t.statuses("python-install"))

    def test_07_an_unverified_download_fails_and_a_checked_one_passes(self):
        (self.t.root / "Containerfile.app").write_text(GOOD_CF + "RUN curl -fsSLo /x.tgz https://example.com/x.tgz\n")
        self.assertIn("FAILED", self.t.statuses("downloads"))
        (self.t.root / "Containerfile.app").write_text(
            GOOD_CF + f"RUN curl -fsSLo /x.tgz https://example.com/x.tgz && echo '{'d' * 64}  /x.tgz' | sha256sum -c -\n")
        self.assertNotIn("FAILED", self.t.statuses("downloads"))

    def test_07b_curl_pipe_shell_in_an_install_script_fails(self):
        (self.t.root / "install").mkdir()
        (self.t.root / "install" / "go.sh").write_text("curl -fsSL https://example.com/i.sh | sh\n")
        self.assertIn("FAILED", self.t.statuses("downloads"))

    def test_07c_apt_against_the_live_mirror_fails(self):
        (self.t.root / "Containerfile.app").write_text(GOOD_CF + "RUN apt-get update && apt-get install -y curl\n")
        self.assertIn("FAILED", self.t.statuses("os-packages"))

    def test_07d_two_different_apt_snapshots_fail(self):
        (self.t.root / "Containerfile.b").write_text(GOOD_CF.replace("20261007T000000Z", "20261001T000000Z"))
        self.assertIn("FAILED", self.t.statuses("os-packages"))


class HashedInstallRefusesSubstitution(unittest.TestCase):
    """#2: a package file whose hash is not the locked one never installs (real pip, offline)."""

    @staticmethod
    def wheel(dirpath: Path) -> Path:
        name = "ctdcdummy-1.0-py3-none-any.whl"
        files = {"ctdcdummy/__init__.py": b"X = 1\n",
                 "ctdcdummy-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: ctdcdummy\nVersion: 1.0\n",
                 "ctdcdummy-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
        rec = []
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
                h = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                rec.append(f"{n},sha256={h},{len(data)}")
            rec.append("ctdcdummy-1.0.dist-info/RECORD,,")
            z.writestr("ctdcdummy-1.0.dist-info/RECORD", "\n".join(rec) + "\n")
        p = dirpath / name
        p.write_bytes(buf.getvalue())
        return p

    def pip(self, d: Path, digest: str) -> subprocess.CompletedProcess:
        (d / "lock.txt").write_text(f"ctdcdummy==1.0 --hash=sha256:{digest}\n")
        return subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(d),
                               "--require-hashes", "--no-deps", "--only-binary=:all:", "--target", str(d / "t"),
                               "-r", str(d / "lock.txt")], capture_output=True, text=True)

    def test_02_a_changed_artifact_hash_fails_the_install(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            w = self.wheel(d)
            good = hashlib.sha256(w.read_bytes()).hexdigest()
            ok = self.pip(d, good)
            self.assertEqual(ok.returncode, 0, ok.stderr[-500:])           # control: the real hash installs
            shutil.rmtree(d / "t")
            bad = self.pip(d, "0" * 64)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("hash", (bad.stderr + bad.stdout).lower())
            self.assertFalse((d / "t" / "ctdcdummy").exists())


@unittest.skipUnless(shutil.which("npm"), "npm not installed")
class NpmLockDivergence(unittest.TestCase):
    def test_05_package_json_and_lock_out_of_sync_fails_npm_ci(self):
        # the real runner frontend lockfile; package.json asks for a react the lock does not hold
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            src = REPO / "src" / "runner" / "frontend"
            shutil.copy(src / "package-lock.json", d)
            pj = json.loads((src / "package.json").read_text())
            pj["dependencies"]["react"] = "18.2.0"
            (d / "package.json").write_text(json.dumps(pj))
            r = subprocess.run(["npm", "ci", "--offline", "--dry-run", "--ignore-scripts", "--no-audit", "--no-fund"],
                               cwd=d, capture_output=True, text=True, timeout=180)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("in sync", r.stderr + r.stdout)

    def test_05b_the_committed_pair_is_in_sync(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            src = REPO / "src" / "runner" / "frontend"
            shutil.copy(src / "package-lock.json", d)
            shutil.copy(src / "package.json", d)
            r = subprocess.run(["npm", "ci", "--offline", "--dry-run", "--ignore-scripts", "--no-audit", "--no-fund"],
                               cwd=d, capture_output=True, text=True, timeout=180)
            self.assertNotIn("in sync", r.stderr + r.stdout)


class ProvenanceAdversarial(unittest.TestCase):
    """#8, #9, #12 against the verifier, with podman replaced by fixtures."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name)
        self.p = load("provenance")
        self.p.STORE = self.store
        self.images = {}
        self.p.inspect_image = lambda ref: self.images.get(ref)
        self.p.verify_source = lambda receipt: [("VERIFIED", "source", "stubbed")]

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, image_id: str, name: str, sbom: bytes = b'{"components":[]}\n'):
        d = self.store / image_id
        d.mkdir(parents=True)
        (d / "sbom.cdx.json").write_bytes(sbom)
        bl = load("buildlib")
        (d / "receipt.json").write_text(json.dumps({"image": {"id": "sha256:" + image_id, "deploy_name": name},
                                                    "sbom": {"digest": bl.sha256_bytes(sbom)}}))
        (self.store / "by-name").mkdir(exist_ok=True)
        (self.store / "by-name" / (self.p.name_key(name) + ".json")).write_text(
            json.dumps({"name": name, "id": "sha256:" + image_id}))

    def statuses(self, ref):
        return [s for s, _, _ in self.p.verify_image(ref, deep=False)]

    def test_baseline_recorded_image_verifies(self):
        self.images["localhost/app:latest"] = {"Id": "1" * 64}
        self.put("1" * 64, "localhost/app:latest")
        self.assertEqual(set(self.statuses("localhost/app:latest")), {"VERIFIED"})

    def test_08_a_modified_sbom_fails(self):
        self.images["localhost/app:latest"] = {"Id": "1" * 64}
        self.put("1" * 64, "localhost/app:latest")
        (self.store / ("1" * 64) / "sbom.cdx.json").write_text('{"components":[{"name":"evil"}]}\n')
        self.assertIn("FAILED", self.statuses("localhost/app:latest"))

    def test_09_another_image_under_a_familiar_tag_fails(self):
        self.put("1" * 64, "localhost/app:latest")                       # what we built and recorded
        self.images["localhost/app:latest"] = {"Id": "2" * 64}            # what the tag points at now
        self.assertEqual(self.statuses("localhost/app:latest"), ["FAILED"])
        with mock.patch("builtins.print"):
            self.assertEqual(self.p.check_deploy("localhost/app:latest"), 1)

    def test_09b_a_receipt_whose_image_id_was_edited_fails(self):
        self.images["localhost/app:latest"] = {"Id": "1" * 64}
        self.put("1" * 64, "localhost/app:latest")
        r = json.loads((self.store / ("1" * 64) / "receipt.json").read_text())
        r["image"]["id"] = "sha256:" + "3" * 64
        (self.store / ("1" * 64) / "receipt.json").write_text(json.dumps(r))
        self.assertIn("FAILED", self.statuses("localhost/app:latest"))

    def test_12_missing_provenance_is_unverified_never_verified(self):
        self.images["localhost/new:latest"] = {"Id": "4" * 64}
        st = self.statuses("localhost/new:latest")
        self.assertEqual(st, ["UNVERIFIED"])
        with mock.patch("builtins.print") as pr:
            rc = self.p.verify(["localhost/new:latest"], running=False, deep=False, as_json=False)
        self.assertEqual(rc, 0)                                           # reported, not failed...
        self.assertTrue(pr.call_args_list[0].args[0].startswith("UNVERIFIED"))  # ...and never shown as VERIFIED

    def test_12b_a_missing_image_fails(self):
        self.assertEqual(self.statuses("localhost/gone:latest"), ["FAILED"])


class SourceBinding(unittest.TestCase):
    """verify_source against a real throwaway git repository."""

    def test_build_input_changed_after_commit_fails_and_head_build_verifies(self):
        p = load("provenance")
        bl = load("buildlib")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            g = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True, check=True)
            g("init", "-q"); g("config", "user.email", "t@example.com"); g("config", "user.name", "t")
            g("config", "commit.gpgsign", "false")
            (r / "MANIFEST.sha256").write_text("x\n")
            (r / "Containerfile").write_text(GOOD_CF)
            (r / "requirements.txt").write_text("requests==2.34.2 --hash=sha256:" + "c" * 64 + "\n")
            g("add", "-A"); g("commit", "-qm", "x")
            head = g("rev-parse", "HEAD").stdout.strip()
            receipt = {"source_commit": head, "source_repo": str(r), "source_tree_status": "clean",
                       "source_manifest_digest": bl.sha256_file(r / "MANIFEST.sha256"),
                       "inputs": {"containerfile": {"path": "Containerfile", "digest": bl.sha256_file(r / "Containerfile")},
                                  "dependency_inputs": [{"path": "requirements.txt", "digest": bl.sha256_file(r / "requirements.txt")}],
                                  "base_images": [{"name": "docker.io/library/python", "digest": DIGEST_A}]}}
            res = {n: s for s, n, _ in p.verify_source(receipt)}
            self.assertEqual(res["build inputs"], "VERIFIED")
            self.assertEqual(res["signed manifest"], "VERIFIED")
            self.assertEqual(res["current source"], "VERIFIED")
            self.assertEqual(res["commit signature"], "FAILED")             # unsigned commit is not accepted
            receipt["inputs"]["dependency_inputs"][0]["digest"] = DIGEST_B   # built from a modified lock
            self.assertIn("FAILED", [s for s, n, _ in p.verify_source(receipt) if n == "build input"])
            receipt["source_manifest_digest"] = DIGEST_B
            self.assertIn("FAILED", [s for s, n, _ in p.verify_source(receipt) if n == "signed manifest"])


class IntegrityCoverage(unittest.TestCase):
    """#10: locks and the build tooling are inside the signed manifest's scope."""

    def test_10_build_integrity_files_are_not_gitignored(self):
        paths = ["build/policy.toml", "build/tools/requirements.txt", "requirements.txt", "requirements.in",
                 "src/ingest/requirements.txt", "scripts/build/provenance.py", "scripts/build/buildlib.py",
                 "config/llama/artifacts.sha256"]
        r = subprocess.run(["git", "-C", str(REPO), "check-ignore", *paths], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "", f"ignored (outside the signed manifest): {r.stdout}")

    def test_10b_sign_manifest_excludes_only_its_own_files(self):
        text = (REPO / "scripts" / "sign-manifest.sh").read_text()
        self.assertIn("ls-files --cached --others --exclude-standard", text)
        excl = [l for l in text.splitlines() if "grep -zvE" in l]
        self.assertTrue(excl)
        for l in excl:
            for word in ("requirements", "build/", "Containerfile", "package-lock"):
                self.assertNotIn(word, l)

    def test_the_real_tree_passes_its_own_policy(self):
        bp = load("build-policy")
        res = bp.run_all(REPO)
        failed = [d for rows in res.values() for s, d in rows if s == "FAILED"]
        self.assertEqual(failed, [])

    def test_rollout_paths_record_provenance_and_gate_deploys(self):
        sr = (REPO / "scripts" / "stack-refresh.sh").read_text()
        so = (REPO / "scripts" / "serialized-rollout.sh").read_text()
        bi = (REPO / "build-images.sh").read_text()
        self.assertIn("provenance.py\" record", sr)
        self.assertIn('hold_image "$image" "provenance-failed"', sr)
        self.assertIn("provenance.py\" record", so)
        self.assertIn("check-deploy", so)
        self.assertIn("provenance.py\" record", bi)
        sweep = (REPO / "scripts" / "scheduled-integrity-sweep.sh").read_text()
        self.assertIn("provenance.py\" verify --running", sweep)
        self.assertIn("build-policy.py\" --quiet", sweep)

    def test_llama_artifacts_are_checked_before_start(self):
        unit = (REPO / ".config/systemd/user/corporatetraveldc-llama.service").read_text()
        pre = [l for l in unit.splitlines() if l.startswith("ExecStartPre=")]
        self.assertTrue(any("sha256sum" in l and "--check" in l and "config/llama/artifacts.sha256" in l for l in pre))
        listed = (REPO / "config/llama/artifacts.sha256").read_text()
        self.assertIn("llama-server", listed)
        self.assertIn(".gguf", listed)


class LockParser(unittest.TestCase):
    def test_parses_markers_extras_and_continuations(self):
        bl = load("buildlib")
        text = ("uvloop==0.23.0 ; sys_platform != 'win32' \\\n    --hash=sha256:" + "a" * 64 + " \\\n"
                "    --hash=sha256:" + "b" * 64 + "\n    # via uvicorn\n"
                "psycopg[binary,pool]==3.3.6 \\\n    --hash=sha256:" + "c" * 64 + "\n")
        entries, problems = bl.parse_lock(text)
        self.assertEqual(problems, [])
        self.assertEqual([(e.name, e.version, len(e.hashes)) for e in entries],
                         [("uvloop", "0.23.0", 2), ("psycopg", "3.3.6", 1)])

    def test_the_real_locks_are_universal_and_complete(self):
        bl = load("buildlib")
        for lock in ("requirements.txt", "src/ingest/requirements.txt", "src/runner/requirements.txt"):
            entries, problems = bl.parse_lock((REPO / lock).read_text())
            self.assertEqual(problems, [], lock)
            names = {e.name for e in entries}
            self.assertIn("fastapi", names, lock)
            self.assertTrue(all(e.hashes for e in entries), lock)


if __name__ == "__main__":
    unittest.main()


class ExternalImagesAreNeverVerifiedAsPinned(unittest.TestCase):
    def test_external_receipt_reports_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            p = load("provenance")
            bl = load("buildlib")
            p.STORE = Path(td)
            sb = b'{"components":[]}\n'
            d = p.STORE / ("5" * 64)
            d.mkdir()
            (d / "sbom.cdx.json").write_bytes(sb)
            (d / "receipt.json").write_text(json.dumps({"kind": "external", "image": {"id": "sha256:" + "5" * 64},
                                                        "sbom": {"digest": bl.sha256_bytes(sb)}}))
            p.inspect_image = lambda ref: {"Id": "5" * 64}
            st = {n: s for s, n, _ in p.verify_image("docker.io/library/nginx:alpine", deep=False)}
            self.assertEqual(st["build provenance"], "UNVERIFIED")
            self.assertNotIn("current source", st)


class FinalPassF1(unittest.TestCase):
    """Final-pass finding F1: the sweep must judge the ACTUAL running image id and every
    local tag (exited oneshots included), not just the tag of running containers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = load("provenance")
        self.p.STORE = Path(self.tmp.name)
        self.p.verify_source = lambda receipt: [("VERIFIED", "source", "stubbed")]
        bl = load("buildlib")
        sb = b'{"components":[]}\n'
        d = self.p.STORE / ("1" * 64)
        d.mkdir()
        (d / "sbom.cdx.json").write_bytes(sb)
        (d / "receipt.json").write_text(json.dumps({"image": {"id": "sha256:" + "1" * 64}, "sbom": {"digest": bl.sha256_bytes(sb)}}))
        (self.p.STORE / "by-name").mkdir()
        (self.p.STORE / "by-name" / "x.json").write_text(json.dumps({"name": "localhost/app:latest", "id": "sha256:" + "1" * 64}))
        ids = {"localhost/app:latest": "1" * 64, "sha256:" + "1" * 64: "1" * 64, "sha256:" + "2" * 64: "2" * 64}
        self.p.inspect_image = lambda ref: {"Id": ids[ref]} if ref in ids else None

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_running_container_on_an_unrecorded_id_fails_even_when_the_tag_is_clean(self):
        # the tag points at the recorded image, but a container is running something else
        self.p.local_containers = lambda: [("app", "localhost/app:latest", "2" * 64, True),
                                           ("job", "localhost/app:latest", "1" * 64, False)]
        with mock.patch("builtins.print"):
            self.assertEqual(self.p.verify([], running=True, deep=False, as_json=False), 1)

    def test_exited_oneshots_tags_are_checked(self):
        self.p.local_containers = lambda: [("job", "localhost/app:latest", "1" * 64, False)]
        with mock.patch("builtins.print") as pr:
            rc = self.p.verify([], running=True, deep=False, as_json=False)
        self.assertEqual(rc, 0)
        self.assertTrue(any("next start" in str(c.args[0]) for c in pr.call_args_list))


class Review06Incident(unittest.TestCase):
    """2026-10-08: a cached amd64 base image made four production builds amd64,
    and one build path recorded no provenance (security review 06)."""

    def test_a_foreign_architecture_image_is_never_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            p = load("provenance")
            p.STORE = Path(td)
            p.inspect_image = lambda ref: {"Id": "6" * 64, "Architecture": "amd64"}
            p.make_sbom = lambda ref, info: (_ for _ in ()).throw(AssertionError("must refuse before the SBOM"))
            with mock.patch.dict("os.environ", {"BUILD_EXPECTED_ARCH": "arm64"}), mock.patch("sys.stderr"):
                self.assertEqual(p.record("localhost/app:latest", None, REPO / "Containerfile.web", REPO), 1)
            self.assertFalse(any(Path(td).iterdir()))

    def test_every_build_command_pins_the_platform_and_records_provenance(self):
        bp = load("build-policy")
        rows = bp.check_build_commands(REPO, {})
        self.assertFalse([d for s, d in rows if s == "FAILED"], rows)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "scripts").mkdir()
            (root / "build-images.sh").write_text("podman build -f Containerfile.x -t localhost/x:latest .\n")
            details = " ".join(d for s, d in bp.check_build_commands(root, {}) if s == "FAILED")
            self.assertIn("without --platform", details)
            self.assertIn("not followed by a provenance record", details)
