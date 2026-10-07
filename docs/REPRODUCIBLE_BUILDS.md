# Build integrity: locked inputs, provenance and their limits

> **Canonical document.** Current state of how runtime images are built, what is pinned, what is recorded, and what is **not** established. Changes after this point keep the old wording struck through with the date, per the repository convention. The pass that produced this document, with its evidence, is the frozen record `docs/security-reviews/2026-10-07-05-reproducible-builds.md`.

This document avoids "reproducible build" as a blanket claim. It uses four assurance levels:

| Level | Meaning | Status here |
|---|---|---|
| 1. Resolver reproducibility | exact application dependency versions are locked | **reached** for every image this repository builds |
| 2. Build-input reproducibility | base images, application dependencies, OS-package inputs and build configuration are pinned or deterministically identified (Python and base images by digest; OS packages by a dated archive snapshot, see its limits) | **reached** for every image this repository builds; see the stated exceptions |
| 3. Artifact provenance | the built image has an id, an SBOM, a source revision, a manifest identity and build metadata that can be verified later | **reached** for images built by the rollout tooling after this change. Images built earlier report UNVERIFIED until their next rebuild |
| 4. Bit-for-bit reproducibility | independent builds from the same declared inputs produce identical bytes | **not claimed**. One experiment came close (see "Bit-for-bit") |

Third-party images (nginx, Postgres, Nextcloud, the SDR feeders and others) are outside levels 1–2 because this deployment does not build them. Their handling is in "Third-party images".

## Threat model

The question this layer answers: **is the code running in a container exactly what was reviewed and signed, built only from inputs that were reviewed?**

| Threat | Before 2026-10-07 | Now |
|---|---|---|
| A dependency release (malicious or just broken) lands in the next routine rebuild without review | possible: `>=` ranges were re-resolved on every build; web and poller, built hours apart from the same file, already differed (`opentelemetry-api` 1.45.0 vs 1.45.1) | a build installs only the hashed lock; a new version needs a lock refresh, which is a reviewed, signed diff |
| A package file on the index is swapped for one with the same version | undetected | the install fails: every file's sha256 is in the lock (`pip --require-hashes`) |
| A base-image tag is re-pointed upstream | the next build silently used it | `FROM` names a digest; a moved tag has no effect until someone refreshes the pin |
| A Debian mirror serves different package versions | silently installed | apt reads a dated snapshot of the archive; the same snapshot yields the same versions |
| Someone retags a different local image under a familiar name | only stack-refresh's audit compared running ids with its own build record | the deploy gate and the integrity sweep compare the image id against the recorded provenance receipt |
| An SBOM or receipt is edited | no SBOM existed | the receipt holds the SBOM digest; the verifier recomputes it and can re-derive the SBOM from the image |
| A lock or Containerfile is edited after signing | the signed-manifest sweep (15 min) | unchanged: every lock, intent file and the build tooling are inside the signed manifest (a `.gitignore` rule that would have excluded `build/` and `scripts/build/` was fixed in the same pass) |

Out of scope: an attacker with the operator's Unix account. That account can rebuild, re-record and re-sign. The receipts are not separately signed (see "Limits").

## Properties

| Property | Status | Enforcement point | Proof |
|---|---|---|---|
| Python resolution is fixed | **ENFORCED** | `pip install --require-hashes --no-deps --only-binary=:all: -r <lock>` in every Containerfile; `build-policy.py` python-install check | adversarial tests 01, 06; `pip check` clean in all 11 trial images |
| Python artifact hashes | **ENFORCED** | the same install flags; pip refuses any file whose sha256 is not in the lock | test 02 (real pip, offline: a substituted file fails, the real one installs) |
| Lock is current with its intent | **ENFORCED** at sign and sweep | `# input-digest:` in each lock vs the `.in` closure; `build-policy.py` locks check; the integrity sweep | tests 01, 01b |
| Node dependency lock | **ENFORCED** | `npm ci` against the committed `package-lock.json` in a builder stage; the runtime image carries only the built static files | test 05 (real `npm ci` refuses a desynced pair) |
| Base-image identity | **ENFORCED** | fully qualified `FROM name:tag@sha256:<index>`; `build-policy.py` base-images check; `pin-base-images.py --verify-remote` | tests 03, 04, 04b, 04c; three indexes verified to carry arm64 and amd64 |
| OS-package reconstruction | **PARTIAL** | `APT::Snapshot` set from `ARG APT_SNAPSHOT` before `apt-get update`; `build-policy.py` os-packages check | test 07c/07d; trial builds installed gnupg 2.4.7-21+deb13u1 from `snapshot.debian.org/…/20261007T000000Z`. Partial because the snapshot service is external and not mirrored here |
| External binary / model verification | **PARTIAL** | `corporatetraveldc-llama.service` `ExecStartPre=sha256sum --strict --check config/llama/artifacts.sha256` (binary, its 18 libraries, the model) | test `test_llama_artifacts_are_checked_before_start`; digests match today. Partial: the files' upstream origin was never recorded, so the digests prove "unchanged since 2026-10-07", not "matches the vendor" |
| Runtime executable downloads | **NONE FOUND in first-party code**; third-party exceptions listed | inventory below; `build-policy.py` downloads check for build paths | repository search, review 05 |
| SBOM | **ENFORCED for locally built images** (generated from the built image) | `provenance.py record` in stack-refresh, serialized-rollout and build-images.sh; failure holds the image | trial: 168 components for web, re-derived identical; test 08 |
| Image provenance receipt | **ENFORCED for locally built images built after this change** | same call sites | tests 09, 09b, 12, `SourceBinding` |
| Deployment artifact binding | **ENFORCED for restarts done by serialized-rollout** (prevention). **DETECTED within 15 minutes** for every other start: timer jobs, crash restarts and manual restarts start from the tag without a gate | `provenance.py check-deploy` before each rollout restart; `provenance.py verify --running` in the integrity sweep checks every running container's actual image id **and** the current image behind every local tag, exited timer jobs included | tests 09, `FinalPassF1`; a name never recorded is UNVERIFIED (pre-provenance), not VERIFIED |
| Bit-for-bit rebuild | **NOT CLAIMED** | none | experiment below |
| Third-party image pinning | **NOT ENFORCED** (tags, auto-updated weekly) | stack-refresh's audit compares running ids with what it pulled; `provenance.py record-external` records an SBOM per pulled image | see "Third-party images" |

## Dependency locking (Python)

Two layers per image:

- **Intent, human-maintained:** `requirements.in`, `src/ingest/requirements.in` (includes the shared file plus Solace), `src/runner/requirements.in`, `requirements-docgen.in`, `src/{acars,ais,utm}_watcher/requirements.in`, and `build/tools/requirements.in` for the host tools. Ranges, comments and the reasons for each dependency live here.
- **Lock, generated:** the `.txt` beside each `.in`. Every transitive package is pinned to one version, with the sha256 of every file published for that version, plus `# input-digest:`, the hash of the `.in` closure it came from. Never edited by hand.

`build/policy.toml` maps each lock to its Python version and the Containerfiles that consume it.

**Generator:** `scripts/build/lock-python.py`, which runs `uv pip compile --universal --generate-hashes --no-build`.
- `--universal` resolves for all platforms at once and writes environment markers, so one lock serves aarch64 and x86_64. This is the reason uv was chosen over pip-tools, which resolves only for the machine it runs on.
- `--no-build` refuses versions that would need a source build. A source build fetches its build tools without hashes.
- uv is used only to generate locks, from a private venv installed out of `build/tools/requirements.txt` (hashed). Images never contain uv: they install the lock with the base image's own pip.

**First generation (2026-10-07)** was seeded with the exact versions running in each image (`--seed-constraints`). The locks therefore match what was deployed and tested; nothing was upgraded in the same step. The only additions over the running sets are marker-gated packages that Linux never installs: `tzdata` (Windows) and `httpx2-jsfetch` (WebAssembly).

**pip itself** is the base image's version. The old `pip install --upgrade pip` floated pip on every build and is gone.

**Architecture:** for every lock set, pip obtained every pinned file for both `aarch64` and `x86_64` with hashes enforced. The one x86_64 surprise, Solace's `manylinux_2_12` wheel, installs on any normal x86_64 Linux. An amd64 build of the web image installed the same versions as arm64; amd64 runtime was not demonstrated (see Supported architectures).

## Base images

Every `FROM` is `docker.io/library/<image>:<tag>@sha256:<digest>`:

- **The digest is the identity.** It is a **multi-platform index digest**, not a single-architecture manifest, so an arm64 host and an amd64 host build from the same reviewed index and each takes its own platform's image from it.
- **The tag stays for humans.**
- **The name is fully qualified**, so the host's short-name resolution (`registries.conf`) cannot redirect it.

| Image | Index digest (2026-10-07) | Platforms in the index |
|---|---|---|
| `python:3.13-slim` | `sha256:bf44cdfcb76c…` | 386, amd64, arm, arm64, ppc64le, riscv64, s390x |
| `python:3.12-slim` | `sha256:05cda9777409…` | same |
| `node:20-alpine` | `sha256:fb4cd12c85ee…` | amd64, arm, arm64, ppc64le, s390x |

Refreshing is a deliberate event: `scripts/build/pin-base-images.py --refresh [name:tag]` rewrites the pins and prints what moved; review, test, sign, rebuild.

## OS packages

| Image | Packages | Why | Runtime need |
|---|---|---|---|
| web, poller, pusher, demo, amtrak-tracker | `gnupg` | the in-image signed-manifest gate (`verify-manifest.sh` uses `gpg --verify`) | yes. The base image ships neither `gpg` nor `gpgv`, so it cannot be dropped |
| ingest | `ca-certificates`, `gnupg` | the gate; TLS to the SWIM brokers | `ca-certificates` is already in the base image; kept explicit |
| docgen | `gnupg`, `libreoffice-writer`, `libreoffice-impress` | the gate; document → PDF | yes |
| runner, watchers | none | — | — |

**Mechanism:** each image runs `printf 'APT::Snapshot "%s";' "$APT_SNAPSHOT" > /etc/apt/apt.conf.d/50snapshot` before `apt-get update`, with `ARG APT_SNAPSHOT=20261007T000000Z`. apt then reads `snapshot.debian.org/archive/debian/<timestamp>` (and `debian-security`), whose signed Release files apt verifies with the base image's Debian keyring. The same base digest plus the same snapshot should yield the same package versions: a snapshot is the archive as it was at that instant, and apt verifies its signed Release files. This is a property of the snapshot service that this deployment relies on, not one it re-checks. The SBOM records what was actually installed. Versions are not pinned by hand, because the snapshot fixes them all consistently, dependencies included.

**Security updates:** move the snapshot with `pin-base-images.py --apt-snapshot now`, review, sign, rebuild. Updates are therefore deliberate events. They are not slower: stack-refresh's weekly rebuild no longer picks them up implicitly, and the refresh is one command.

**Limits:**
- `snapshot.debian.org` is a third-party service. If it is down, builds fail closed; nothing falls back to the live mirror. No local mirror is kept.
- The pin is a timestamp, not a package-list digest. The SBOM records what was actually installed.

## External artifacts

| Artifact | Upstream | Version | Digest recorded | Verified where | Update |
|---|---|---|---|---|---|
| `llama-server` + 18 libraries (`/usr/local/lib/ollama/`) | llama.cpp, installed with the former Ollama bundle on 2026-08-13 | `version: 1 (0b1bad14f)`, Clang 21.1.8, aarch64 | yes, `config/llama/artifacts.sha256` | the llama unit's `ExecStartPre`, every start: a mismatch means no start | replace deliberately, regenerate the digest file, review, sign |
| `qwen3-4b-instruct-2507-q4_0.gguf` | **origin not recorded** (installed 2026-09-21) | Qwen3-4B-Instruct-2507, q4_0 | yes, same file | same | same |
| `phi3-mini-q4_0.gguf` | not recorded | — | no: no unit loads it | — | candidate for removal |
| Debian packages | snapshot.debian.org | snapshot `20261007T000000Z` | via apt's signed Release files | at build | `--apt-snapshot` |
| PyPI files | pypi.org | per lock | sha256 per file in the lock | at build (pip) | `lock-python.py` |
| npm packages | registry.npmjs.org | `package-lock.json` | sha512 integrity per package | at build (`npm ci`) | `npm install` locally, review the lock diff |

Nothing is downloaded at build time outside these paths: the policy check scans every Containerfile and install script for `curl`, `wget`, `ADD <url>` and stray `pip install`. Three retired proof-of-concept installers (`install/install*.sh`, `install/install-windows.ps1`) still contain `curl | sh` and unpinned installs. They are listed as named, dated exceptions in `build/policy.toml`: kept for history, not a supported build path.

## Runtime downloads

First-party code (`src/`, `scripts/`) installs nothing and executes nothing it downloads. Every `curl` in `scripts/` is an API or health call.

| Behaviour | Class |
|---|---|
| SWIM, NWS, FAA, Amtrak, MARC/VRE, RSS, research and news ingestion | REQUIRED MUTABLE DATA (content, not code) |
| FAA CIFP / registry tables pulled on schedule | REQUIRED MUTABLE DATA |
| llama model and binary | PINNED RUNTIME ARTIFACT (digest-checked at start; origin unrecorded) |
| Persona Modelfiles (`corporatetraveldc.*`) | OPERATOR-MANAGED DATA. Signed text; the `FROM phi3:mini` lines are inert since the Ollama retirement |
| Weekly pull of third-party images (stack-refresh) | SECURITY EXCEPTION: deliberate, for upstream security fixes; audited, SBOM recorded |
| Open WebUI tools/functions (can pip-install at runtime inside its container) | SECURITY EXCEPTION, third-party container |
| Nextcloud app store updates | SECURITY EXCEPTION, third-party container |
| Claude Code CLI self-update (operator and agent accounts) | OPERATOR-MANAGED; host tooling, not a runtime image |

## SBOM

`scripts/build/provenance.py` produces a **CycloneDX 1.5 JSON** SBOM **from the built image**:
- it runs the image offline (`--network none`) and reads `dpkg`, `apk` and Python distribution metadata inside it;
- components carry package URLs (`pkg:deb/…`, `pkg:apk/…`, `pkg:pypi/…`).

It answers "what ended up in this image", not "what the source intended". For example, the trial web image lists 108 Debian and 60 Python packages, `pip` 26.2.1 among them.

The output is deterministic (in review 05, an SBOM re-derived from the same image was byte-identical):
- no generation timestamp inside it;
- a serial number derived from the image id;
- components sorted.

The verifier can therefore re-derive it from the image and compare digests.

**Limits:**
- The SBOM lists package-manager-visible contents only. Software compiled into an image outside a package manager is not listed: in third-party images, for example, the SDR decoders.
- Node packages of the runner's builder stage are not in the runtime image, so they are not in the SBOM. They live in `package-lock.json`.

## Provenance receipts

**Written** by `provenance.py record` after each successful build:
- in stack-refresh, after the in-image gate;
- in serialized-rollout;
- in `build-images.sh`.

A failure to record is treated as a failed build: the image is held.

**Stored** under `BUILD_PROVENANCE_DIR`, by default `~/.local/state/build-provenance`:
- `<image-id>/receipt.json` and `<image-id>/sbom.cdx.json`;
- `by-name/` (the last recorded image per deployment name);
- `ledger.jsonl` (append-only history).

Canonical JSON (sorted keys). Fields:

| Field | Meaning |
|---|---|
| `source_commit`, `source_tree_status` | the commit built, and whether the tree was clean |
| `source_manifest_digest`, `source_manifest_signature_digest` | sha256 of `MANIFEST.sha256` and its `.asc` at build time |
| `build_policy_digest` | sha256 of `build/policy.toml` |
| `inputs.containerfile` | path and sha256 |
| `inputs.dependency_inputs` | every lockfile the Containerfile copies: path and sha256 |
| `inputs.base_images` | each `FROM`: name, tag, index digest |
| `inputs.apt_snapshot` | the Debian snapshot timestamp |
| `image.id`, `image.manifest_digest` | the content-addressed result |
| `sbom.digest`, `sbom.components` | the SBOM's sha256 and size |
| `builder` | podman and buildah versions |
| `architecture`, `build_timestamp`, `source_date_epoch` | build metadata |
| `source_repo` | the local checkout path. Deployment-private; receipts are never published |

**Relationship to the signed manifest.** No second trust root. The chain is:

```
reviewed source tree ── signed by the operator (MANIFEST.sha256.asc, signed commit)
  └ locks, Containerfiles, build/policy.toml ── inside that manifest
      └ base digests + apt snapshot ── written in the Containerfiles
          └ podman build ── image id (content address)
              └ SBOM (from the image) ── digest in the receipt
                  └ receipt ── names the commit, the manifest digest, every input digest, the image id
                      └ deploy gate + integrity sweep ── running image id must have a matching receipt
```

An auditor answers the six questions as follows:

| Question | Answered by |
|---|---|
| What source was reviewed? | the signed commit, plus the manifest digest in the receipt; the verifier checks the commit's signature status and that its `MANIFEST.sha256` hashes to the recorded digest |
| What dependencies were authorized? | the lock at that commit; the verifier checks that the receipt's lock digest equals the file at that commit |
| What base image was used? | the index digest in the receipt, checked against the Containerfile at that commit |
| What artifact was produced? | the image id |
| What actually landed in it? | the SBOM; with `--deep`, the verifier re-derives it from the image |
| Is that what was deployed? | `check-deploy` before each restart; `verify --running` every 15 minutes |

Signing the source alone does not answer questions 4–6. The receipt and the running-image check do.

## Verification

`scripts/build/build-policy.py` runs offline over the repository. Its checks:
- locks;
- base images;
- Python install;
- OS packages;
- node;
- downloads;
- build commands, so that no `--build-arg` can override a pin.

`scripts/build/provenance.py verify [--running] [--deep]` checks per image:
- receipt present;
- image id;
- SBOM digest;
- SBOM against the artifact (`--deep`);
- source tree clean;
- commit signature;
- signed manifest;
- build inputs at that commit;
- base images;
- whether the image was built from the current HEAD.

Statuses are VERIFIED, FAILED, UNVERIFIED and NOT APPLICABLE:
- **FAILED** exits 1.
- **UNVERIFIED** is printed as UNVERIFIED and never as success. It covers an image built before provenance existed, or one built from an older signed commit. An older commit is normal between a docs-only commit and the next weekly rebuild.

The integrity sweep (every 15 minutes) runs both. FAILED fails the sweep with a priority-5 push; the UNVERIFIED count is logged each run.

## Deployment identity

Quadlets still name `localhost/<image>:latest`. Rewriting 50+ unit files to an image id on every build would make every routine rebuild a signed change to the unit files. Instead, the binding is enforced at the two points where a tag turns into a running container:

- **serialized-rollout** runs `check-deploy` before restarting each locally built unit. The tag must resolve to an image id that has a receipt. If the name has receipts but this id does not (a substituted tag), the unit is HELD. This is prevention, but only for rollout restarts.
- **The sweep** (every 15 minutes) checks every running container's actual image id and the image behind every local tag that any container uses, exited timer jobs included. This is detection: a timer job, crash restart or manual restart that starts from a substituted tag is not blocked, and is reported at the next sweep at priority 5.

The image id is a content address, so a substituted image cannot carry the original id.

## Update workflow

```
discover (dependency-audit, Dependabot, upstream notice)
  → lock-python.py --upgrade-package X     (or pin-base-images.py --refresh / --apt-snapshot now)
  → review the diff                         (lock lines and digests are the whole change)
  → dependency-audit.py --repo .            (known vulnerabilities)
  → tests + build-policy.py
  → sign (sign-manifest + signed commit)
  → rebuild (stack-refresh / serialized-rollout: build → gate → SBOM → receipt)
  → deploy (check-deploy before each restart)
  → sweep verifies the running ids
```

Security fixes are not delayed by locking: a lock refresh is one command plus review, and the daily dependency audit and Dependabot alerts flag when one is needed. What locking removes is the *unreviewed* update. A rebuild never picks up a new version on its own.

## Supported architectures

- **Platform:** arm64 (the reference deployment) and amd64.
- **Locks:** universal; all verified fetchable and hash-matching on both architectures.
- **Base pins:** multi-platform indexes carrying both architectures, verified against the registry.
- **Build on amd64:** under emulation, the web image built, passed `pip check`, and installed exactly the same 59 Python package versions as the arm64 build.
- **amd64 runtime:** **not demonstrated.** This host's 16 KiB-page kernel cannot run some x86 native libraries under emulation (psycopg's bundled libraries fail to map). It needs a real x86_64 host.
- **Not covered:** other architectures in the indexes (386, arm v7, ppc64le, riscv64, s390x) were not tested.

## Bit-for-bit

Not claimed. The experiment, on the smallest image (acars-watcher) built twice with `--no-cache` on the same host:

| Variant | Result |
|---|---|
| production flags | base layers identical; all four built layers differ |
| `--source-date-epoch` + `--rewrite-timestamp` | 3 of 4 built layers identical; the `pip install` layer differs in 82 files, all compiled bytecode (`.pyc`) |
| the above + `pip install --no-compile` | **identical image ids** |

So:
- the inputs are fixed;
- the remaining nondeterminism is timestamps and bytecode generation;
- the `build-date` labels the rollout tooling writes also differ per build by design.

Production keeps compiling bytecode, because skipping it moves compilation to every container start. That trade-off has not been decided. Even the identical result came from one builder, not independent ones, so it is not a Level 4 demonstration.

## Third-party images

16 images (nginx ×5 units, Postgres ×2, Nextcloud, ntfy, Open WebUI, Proton Mail Bridge, RSS-Bridge, nine SDR-feeder images) run from tags:

- 13 are `AutoUpdate=registry`.
- stack-refresh pulls them weekly, deliberately, for upstream security fixes.
- Postgres changes only through `safe-pg-image-update.sh`.
- A Nextcloud major-version jump is held.

**Recorded:**
- stack-refresh's audit compares each running container's image with what it pulled;
- since this change, `provenance.py record-external` keeps an SBOM and the registry digest per image id. It is informational: the verifier reports these as UNVERIFIED ("third-party image… inputs are not pinned here").

**Not established:**
- pinning;
- review before update;
- vulnerability scanning of these images. The SBOMs make scanning possible; no scanner is installed. Trivy 0.69.3 and Grype 0.117 are available as Fedora packages.

## Repurposeability

**Product build policy, the same for any deployment:**
- `build/policy.toml`;
- the locks;
- the Containerfiles;
- `scripts/build/`.

None of the new tooling names an organization, user, host, network or architecture. The project name defaults to the repository directory name.

**Deployment profile, supplied by the deployment:**

| Variable | Default | Purpose |
|---|---|---|
| `BUILD_PROVENANCE_DIR` | `~/.local/state/build-provenance` | receipts and SBOMs |
| `BUILD_TOOLS_VENV` | `~/.local/share/build-tools/venv` | uv for lock generation |
| `BUILD_PROJECT` | repository directory name | project field in receipts |

Image names come from the deployment's own rollout lists, and the llama artifact paths are this deployment's (`config/llama/artifacts.sha256`).

## Limits

- **Receipts are not signed.** Their integrity rests on the image id (a content address) and on the signed commit they name. The operator account, or anything running as it, can write a matching receipt for an image it built. That is the same trust boundary as the signing key (`docs/AGENT_TRUST_MODEL.md` §0). Signing receipts with the approval key is the next step.
- The Debian snapshot service and the registries are external. Their unavailability fails builds closed.
- **Model and binary origin was never recorded.** Integrity from today on is enforced; authenticity is not.
- **Images already running were built before this change.** They report UNVERIFIED until stack-refresh or the rollout rebuilds them.
- **Third-party images:** see above.
- The **[operator LLC abbreviation]utive contact API** image (website repository) still uses `python:3.12-slim` by tag and exact pins without hashes. It gets a receipt, because its build goes through stack-refresh, but its Containerfile is not under this policy yet.
- The **Executive Standard verifier** runs code bind-mounted from the website repository, outside this repository's manifest (open finding S1, `docs/FINDINGS_2026-10-06.md`).
