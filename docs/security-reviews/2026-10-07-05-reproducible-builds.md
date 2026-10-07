# Security review 05 — build inputs, provenance and reproducibility (2026-10-07)

> **Review record. Frozen once committed.** Part of the security review chain (`README.md` in this directory). It follows `2026-10-07-04-dependency-scanning.md`. The canonical document this pass produced is `docs/REPRODUCIBLE_BUILDS.md`. Later findings get a new record; this one is not edited.

## Scope

- **Starting point:** commit `320ce6378cc2b86c514478ffc368d65e9511810b` (tree `7260d6b747e6`), signed and pushed. The changes below are on top of it and are signed in the commit that adds this record.
- **Brief:** an external reviewer's "reproducible builds and supply-chain hardening" brief, with Phases 1–18 and a final adversarial pass. Its rule was "do not make the build look more reproducible on paper".
- **Out of scope by design:** no change to the agent gateway, approvals, console or audit architecture.

## Starting gaps

| Gap | Evidence |
|---|---|
| Python dependencies re-resolved on every build | root, runner and ingest requirements were all `>=` ranges, with no hashes. **web and poller, built hours apart from the same file, already ran different `opentelemetry-api` versions (1.45.0 vs 1.45.1)** |
| pip itself floated | `pip install --upgrade pip` in 6 Containerfiles |
| Ingest resolved twice | the shared ranges, then Solace, as two separate resolutions |
| docgen ranges inline in its Containerfile | `requests>=2.32.0` etc. |
| Watchers pinned without hashes; transitive dependencies unpinned | `requests==2.34.2 urllib3==2.8.0` |
| Base images by tag | `python:3.13-slim`, `python:3.12-slim`, `node:20-alpine`, as short names that the host's registries.conf resolved |
| OS packages from the live Debian mirror | `apt-get update` in 7 images |
| No SBOM | — |
| No provenance beyond stack-refresh's built-ids list | that list records ids, not inputs or source |
| llama binary, libraries and model unverified | no digest anywhere; origin unrecorded |
| **`.gitignore` excluded every directory named `build/`** | found in this pass. The new `build/policy.toml`, the tools lock and `scripts/build/` would have been invisible to git **and to the signed manifest** (`sign-manifest.sh` uses `--exclude-standard`). Fixed with anchored negations. Test `test_10_build_integrity_files_are_not_gitignored` |
| `dependency-audit.py` skipped `build/` directories and installed `pip-audit` unpinned | fixed: it now audits `build/tools/requirements.txt` and installs from it |

## Build surfaces inventoried

| Component | Build file | Base (before → after) | App deps (before → after) | OS deps (before → after) | Deploy |
|---|---|---|---|---|---|
| web, poller, pusher, demo, amtrak-tracker | `Containerfile.<svc>` | `python:3.13-slim` → `docker.io/library/python:3.13-slim@sha256:bf44…` | `requirements.txt` ranges → hash lock (61) | gnupg, live mirror → snapshot | quadlets `:latest`; stack-refresh / serialized-rollout |
| ingest (7 units) | `Containerfile.ingest` | same | two range resolutions → one hash lock (62) | ca-certificates + gnupg → snapshot | same |
| runner (+ runner-demo) | `Containerfile.runner` | `node:20-alpine` + `python:3.13-slim` → both by digest | `npm ci` (already locked); runner ranges → hash lock (28) | none | same |
| docgen | `Containerfile.docgen` | as web | inline ranges → hash lock (11) | gnupg + LibreOffice → snapshot | on demand |
| acars-watcher | `src/acars_watcher/Containerfile` | `python:3.12-slim` → by digest | exact, no hashes → hash lock (5) | none | quadlet |
| ais-, utm-watcher | their Containerfiles | same | same | none | **not deployed** (fixed anyway, per the dormant-code rule) |
| build-images.sh | manual build of 5 images | — | — | — | now records provenance |
| stack-refresh, serialized-rollout | the rollout builders | — | — | — | now record provenance; serialized-rollout gates restarts |
| llama.cpp server | host binary + model | — | — | — | user unit; now digest-checked at start |
| 16 third-party images | upstream | tags, 13 auto-updated weekly | — | — | unchanged; SBOM per pulled id now recorded |
| `install/install*.sh`, `install-windows.ps1` | retired PoC installers | — | `curl \| sh`, unpinned pip | — | marked retired; listed policy exceptions |
| contact API (website repo) | its Containerfile | `python:3.12-slim` by tag | exact pins, no hashes | — | gets receipts; its Containerfile is **not yet** under the policy |
| `build-models.sh`, Modelfiles | verifier only since the Ollama retirement | — | — | — | data, not a build path |

## Changes

- **Locks:** `requirements.in` (intent, history kept via `git mv`) → generated `requirements.txt`. The same applies to ingest, runner, docgen, the three watchers and the host tools. Each lock records the `# input-digest:` of its intent closure.
  - Generator: `scripts/build/lock-python.py` (`uv pip compile --universal --generate-hashes --no-build`).
  - First generation was seeded from each running image's `pip freeze`, so the locks equal what was deployed. The only additions are marker-gated packages that Linux never installs.
- **Containerfiles (11):**
  - fully qualified, multi-platform index digests;
  - the canonical install line `pip install --no-cache-dir --require-hashes --no-deps --only-binary=:all: -r <lock>`;
  - `APT::Snapshot` from `ARG APT_SNAPSHOT=20261007T000000Z`;
  - the old lines kept as `SUPERSEDED 2026-10-07` comments.
- **`build/policy.toml`:** the product build policy plus the lock-to-consumer map. **`scripts/build/build-policy.py`:** the static checks.
- **`scripts/build/pin-base-images.py`:** `--list`, `--verify-remote`, `--refresh`, `--apt-snapshot`.
- **`scripts/build/provenance.py`:**
  - the CycloneDX 1.5 SBOM from the built image;
  - the receipt;
  - `check-deploy`, `verify`, `record-external`.
- **Wired into:**
  - `stack-refresh.sh`: record after the in-image gate (failure holds the image), plus external SBOMs;
  - `serialized-rollout.sh`: record after each build, `check-deploy` before each local restart;
  - `build-images.sh`;
  - `scheduled-integrity-sweep.sh`: policy plus `verify --running`; FAILED fails the sweep at p5.
- **llama:** `config/llama/artifacts.sha256` (binary, 18 libraries, model); the unit's `ExecStartPre` warm read is now `sha256sum --strict --check`, the same single read of the model.
- **Supporting changes:**
  - `.gitignore`: the anchored `build/` fix;
  - `dependency-audit.py`: audits locks by exact pin, audits `build/`, sets up from the tools lock;
  - `docs/DESIGN-PRINCIPLES.md`: the dependency gate now names `requirements.in`; the old wording is struck through;
  - the retired installers are marked.

## Tests and evidence

**Adversarial tests** in `tests/scripts/test_build_integrity.py`, 32, all passing. Numbering follows the brief:

| # | Attempt | Result |
|---|---|---|
| 1 | change a direct requirement without refreshing the lock | locks check FAILED, "STALE" (also when an included `.in` changes; also for an unhashed, unpinned or index-override line) |
| 2 | change a transitive artifact's hash | real `pip --require-hashes`, offline: the substituted file is refused and nothing is installed; control: the real hash installs |
| 3 | replace one pinned base digest | FAILED: "pinned to a different digest than in …" |
| 4 | mutable base tag / short name / ARG-substituted tag | FAILED in all three |
| 5 | `package-lock.json` inconsistent with `package.json` | real `npm ci --offline` on the runner's lockfile refuses: "can only install packages when … in sync"; control: the committed pair passes |
| 6 | new `pip install` outside the lock / installing a lock not declared for that Containerfile | FAILED |
| 7 | `curl` download without a digest / `curl \| sh` in an install script / apt against the live mirror / two snapshots | FAILED; the same download with `sha256sum -c` passes |
| 8 | modify the SBOM | FAILED (digest mismatch) |
| 9 | another image under a familiar tag / an edited receipt id | FAILED; `check-deploy` exits 1 |
| 10 | modify a lock after signing | the existing manifest sweep covers it. New tests prove the locks and tooling are inside the manifest's scope (not gitignored; `sign-manifest.sh` excludes only its own files) |
| 11 | per-architecture builds | live, below |
| 12 | verifier with missing provenance | UNVERIFIED (never VERIFIED); a missing image is FAILED; a third-party image is never reported as pinned |

Also: `SourceBinding` runs `verify_source` against a real throwaway git repository, covering inputs, manifest digest, current-source detection, the unsigned-commit refusal and a modified lock.

**Live checks (2026-10-07, on this host):**

- **Trial builds of all 11 images** from the working tree, under throwaway tags: all built (11–158 s each). `pip check` reports "No broken requirements" in all 11: every installed package's declared dependencies are present on arm64, so the locks are complete for that platform despite `--no-deps`. Import smoke passed for all 18 direct dependencies in web and for Solace in ingest. `gpg` 2.4.7 was installed from `snapshot.debian.org/…/20261007T000000Z`.
- **Architectures:**
  - every lock set fetched with hashes enforced, wheels only, for both `aarch64` and `x86_64`;
  - the first x86_64 run flagged Solace, but only because the check script omitted the `manylinux_2_12` tag; PyPI has that wheel and it installs;
  - the three base pins are multi-platform indexes with arm64 and amd64 (`pin-base-images.py --verify-remote`);
  - amd64 build of the web image under emulation: see "amd64 build" below.
- **SBOM and receipt for the trial web image:**
  - 168 components (108 deb, 60 PyPI);
  - re-derived from the image, identical;
  - `verify --deep` correctly **FAILED** the trial image as "built from a dirty tree" with its Containerfile and lock differing from the signed commit. That is the property working: only committed, signed sources verify.
- **Third-party SBOMs:** nginx (71 apk), Postgres (44 apk), ultrafeeder (218 deb, 9 PyPI).
- **llama:** `sha256sum --strict --check config/llama/artifacts.sha256` passes on today's files.
- **Policy over the real tree:** VERIFIED; the three retired installers show as NOT APPLICABLE with their exception text.
- **`dependency-audit.py --repo .`:** 9 targets clean, the tools lock included.

**Bit-for-bit experiment** (acars-watcher, two `--no-cache` builds each, same host):
- production flags: all four built layers differ;
- `--source-date-epoch` + `--rewrite-timestamp`: three of four identical; the `pip install` layer differs in 82 files, every one a `.pyc`;
- adding `pip install --no-compile`: **identical image ids** (`35632602da25…`).

Not adopted, and not a Level 4 claim: one builder, one image variant, and moving bytecode compilation to container start is an undecided runtime trade-off.

### amd64 build

- **Built:** `Containerfile.web` with `--platform linux/amd64`, under user-mode emulation (`qemu-x86_64`) on this arm64 host. The build succeeded in 978 s with the same hashed lock, the same index digest (its amd64 member) and the same apt snapshot (gnupg 2.4.7-21+deb13u1).
- **Checked:**
  - `uname -m` in the image: `x86_64`;
  - `pip check`: no broken requirements;
  - `pip freeze`: **identical to the arm64 build**, 59 packages.
- **Not established:** amd64 **runtime** of native extensions. Importing psycopg's binary driver failed under emulation ("failed to map segment from shared object"), and a direct load of its bundled `libcom_err` crashed the emulator with SIGBUS. The cause is the host: this kernel uses **16 KiB pages** (`getconf PAGESIZE` = 16384), and user-mode emulation cannot always map x86 libraries laid out for 4 KiB pages.

So the evidence covers an amd64 build with identical, hash-verified inputs. It does not cover amd64 runtime behaviour, which needs a real x86_64 host. Pure-Python imports were not separately tested under emulation.

## Final adversarial pass

After implementation, a separate hostile reading of the result.

**F1 (found, fixed, tested).**
- **The gap:** the canonical document first said deployment binding was "ENFORCED at restart". It was enforced only for restarts done by serialized-rollout. Timer jobs (about 40 poller oneshots), crash restarts and manual restarts start from the tag with no gate.
- **The sweep missed it too.** It checked the *tag* of running containers, so it missed two cases:
  1. a container whose tag moved after it started;
  2. a oneshot that started from a substituted tag and exited between sweeps.
- **Fix:** `provenance.py verify --running` now checks every running container's actual image id **and** the current image behind every local tag used by any container, exited ones included (`local_containers`, tests `FinalPassF1`).
- **The document now says** prevention for rollout restarts and detection within 15 minutes for every other start.

**Word audit.** Every occurrence of reproducible, deterministic, immutable, pinned, exact, verified, guaranteed, identical, locked and tamper-evident in `docs/REPRODUCIBLE_BUILDS.md` and in this record was matched to an enforcement point or a test:

| Word | Where | Result |
|---|---|---|
| "pinned or deterministically identified" | Level 2 | **weakened**: OS packages are identified by a dated snapshot whose immutability is the service's property, not one this deployment re-checks; now stated |
| "deterministic" | the SBOM | kept, with the evidence: re-derived byte-identical in this pass |
| "ENFORCED" | deployment binding | **weakened** (F1) |
| "proves the locks are complete" | this record | **weakened** to "complete for arm64": `pip check` ran on arm64 images |
| "identical" | bit-for-bit | kept, with "one builder, one variant, not Level 4" |
| "immutable", "guaranteed", "tamper-evident" | — | not used |

**If every upstream mutable tag, index, package repository and download URL changed tomorrow, what could be reconstructed from the recorded provenance?**
- **Python packages:** fully, if the same files are still published, because the lock names every version and every file hash. If PyPI removed a file, the build fails instead of substituting.
- **Base images:** fully, while the registry retains the index digest. A re-pointed tag has no effect, because the digest is used. If the digest were deleted upstream, it is not reconstructible: no local registry mirror is kept, though the image may still be in local storage.
- **Debian packages:** while `snapshot.debian.org` keeps the snapshot. Its purpose is permanent retention, but it is not mirrored here.
- **npm packages:** while the registry keeps those versions; integrity hashes are in the lock.
- **Third-party images:** no. They float on tags. The recorded SBOM and registry digest say what ran, but the image can be rebuilt only if the registry still holds that digest.
- **The llama binary and model:** no. The digests show what ran, but their origin was never recorded, so a deleted copy cannot be re-obtained and confirmed.
- **What every case keeps:** the SBOM and receipt always say *what* ran, even where it can no longer be rebuilt.

**If an attacker can modify a familiar local image tag but not the recorded provenance or signatures, will deployment or verification detect the substitution?**
- **Yes, for any name that has a receipt.** A rollout restart refuses it: `check-deploy` FAILED, unit held. Any other start runs it, and the next integrity sweep reports FAILED at priority 5, within 15 minutes, now including exited timer jobs (F1).
- **Not before the image's first recorded build.** Until then the name has no receipts and reports UNVERIFIED, so substitution is indistinguishable from "built before provenance". This applies to every image running at the moment this change deploys; the first rollout after deploy closes it.
- **Not against the operator account.** An attacker who can also write the provenance store (that account) can record a matching receipt. Receipts are not signed.

## Remaining mutable inputs

- third-party images (tags, weekly pulls);
- `snapshot.debian.org` and the registries as services (availability, not content);
- the unrecorded origin of the llama binary and model;
- `.pyc` bytecode and timestamps (the only measured nondeterminism in a locally built image);
- the contact-API Containerfile in the website repository;
- Claude Code CLI self-updates on the host.

## Assurance level achieved

- **Locally built images:** **Level 1** and **Level 2**. **Level 3** for images built by the rollout tooling after this change is deployed. Running images built before it report UNVERIFIED until rebuilt.
- **Level 4:** not achieved.
- **Third-party images:** below Level 1 for their inputs; identity and contents are recorded.
