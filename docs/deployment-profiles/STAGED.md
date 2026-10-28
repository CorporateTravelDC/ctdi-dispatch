# Staged: parameterized deployment profiles (multi-vertical repurposing)

<del>**Staged 2026-10-08 for later work. Not wired into anything.** Nothing in the platform reads these files; no loader, adapter, policy or test uses them yet.</del> Superseded 2026-10-09: see "Status, 2026-10-09" below. The pack's own README states the same: design-stage contracts, not deployable configuration.

## Provenance

- Received from the operator as `ctdi-parameterized-deployment-templates.zip` (sha256 `0738d34c2ab8834059f6e4a1d3a8967636b7cb92ffd5ce1ac59126a0e131f01c`) (unverified 2026-10-25: the zip is no longer on the box, so the hash cannot be recomputed).
- Files copied unchanged: `README.md`, `validate_profiles.py`, `schemas/profile.schema.json`, `profiles/*.json` (base plus eight verticals: chauffeur-regulated, executive-assistant-chief-of-staff, content-creator, independent-journalist, cfo, cmo, cpa, gig-worker). (2026-10-15: no longer byte-identical to the zip. The schema, profiles and validator were changed on 2026-10-09 (see "Status, 2026-10-09"), and `README.md` carries a dated status note at the top since 2026-10-15. The zip's sha256 above identifies the original.)
- Reviewed on staging: placeholders only (`CHANGE_ME`, `example-*`, null coordinates); no secrets, credentials, personal data or real identifiers. `python3 validate_profiles.py` → "Checked 9 profiles / PASS: schema and security invariants". (2026-10-15: "Checked 11 profiles / PASS"; the validator now also checks `src/common/profiles/` and `private/`.)

## Notes for when the work starts (observations, nothing changed)

1. <del>**Architecture default:**</del> **Resolved 2026-10-09** (profiles now say `host`). **Architecture default:** every profile sets `build.target_platforms` to `linux/amd64`; the reference deployment is `linux/arm64`, and builds pin the host platform (`docs/REPRODUCIBLE_BUILDS.md`, review 06). A loader should derive the platform from the host or require it explicitly.
2. <del>**Closed objects:**</del> **Resolved 2026-10-09** (open maps: env-variable names, region lists, scope lists). **Closed objects:** `sources.credentials_ref`, `sources.source_regions` and `security.agent_scopes` are declared with no properties and `additionalProperties: false`, so the schema accepts only `{}` there. Real profiles need them to be open maps (credential references by name, per-agent scopes).
3. <del>**Validator:**</del> **Resolved 2026-10-09** (missing `jsonschema` is exit 2; `--structural-only` says so). **Validator:** the strict schema check runs only when `jsonschema` is importable and passes silently otherwise; the structural checks always run.
4. **Overlap with existing work:** the README's gate 2 (geography provider) is the same set of hardcoded DC sets that security review 09 Test F and `docs/REGIONALIZATION.md` §2 list (SMES/surface airports, METAR stations, DC facility sets); gate 6 (absent receiver coordinates are an error) differs from today's behaviour (falls back to DC coordinates); gate 8 maps onto the signed-approval mechanism (`src/common/governance.py`) and the agent gateway's per-connector identity.

## Status, 2026-10-09

- **Schema:** `credentials_ref`, `source_regions` and `agent_scopes` are open maps; `build.target_platforms` accepts `host` (build for the building machine) or an explicit `linux/amd64|arm64`; airports are typed objects (`icao`, `roles`, `latitude`, `longitude`, `tz`); new optional `home_base.coordinates_env` (the NAMES of the environment variables holding the receiver position — never the values), `airspace.notam_zones`, `atc_facilities`, `geofence_radius_nm`, `deployment.day_rollover`.
- **Reference profile (public, known-good default):** `src/common/profiles/ctdi-reference.json` reproduces the current Washington, DC behaviour exactly; tests pin it to the remaining hardcoded sets so it cannot drift while consumers migrate. NOTAM zones ship empty, as the public edition always has.
- **Live profile (private ledger):** `docs/deployment-profiles/private/ctdi-dc-live.json`, dropped from the public mirror. Same as the reference plus this deployment's NOTAM zones, agent scopes and integrations. No secrets or personal data: the receiver position stays in the environment. (added 2026-10-25: no process loads it -- `DISPATCH_PROFILE` is set in no live env file and no Quadlet, so every consumer gets the bundled reference profile, `src/common/deployment_profile.py:185`.)
- **Typed loader:** `src/common/deployment_profile.py` — `DISPATCH_PROFILE` selects a profile, else the reference; unknown keys and wrong types are errors; standard library only. Precedence: environment setting > profile > reference.
- **Geography provider:** the same module answers airports by role, airport lookup, airports within a radius and the receiver position, from the profile only. Coordinates and zones are resolved when a profile is written (public airport reference data; OpenStreetMap/Nominatim for free-text places, one request per place under its usage policy), never at runtime.
- **First consumer:** the METAR fetcher's station list (identical default). Also fixed: any 4-character ICAO station now parses (non-US stations used to become `UNKN`).
- **Next consumers, one at a time:** surface-tracking airports (SMES/STDDS), the DC-area and facility sets, the geo filter's centre and radius, NWWS offices, NOTAM zones, FIDS airports and their airport-local zone, the receiver position (making a missing position an explicit error, pack gate 6).

## Status, 2026-10-15

- **Consumers:** still one. `src/poller/fetchers/metar.py` is the only module that imports `common.deployment_profile` (checked at main 1491bf5a). The "next consumers" list above is unchanged and is backlog item 4 (after the 2026-10-12 time-fidelity checkpoint); gate 6 (a missing receiver position is an error) is not implemented, so the DC fallback still applies.
- **Compositions:** the skeleton from `compositions/EMCOMM-HAZARD-COMPOSITION-SPEC.md` §3 shipped on 2026-10-10 (`src/common/composition.py`, `tests/common/test_composition.py`, `compositions/emcomm-cert-hazard-operations.json`). Nothing reads compositions at runtime.
- **Public mirror:** `private/` is still dropped by `scripts/scrub-public-tree.py`.

## Status, 2026-10-20

- **EA and CoS split** (operator 2026-10-20: the Executive Assistant and the Chief of Staff are distinct executive-suite positions): ~~`profiles/executive-assistant-chief-of-staff.json`~~ is replaced by `profiles/executive-assistant.json` and `profiles/chief-of-staff.json`; the zip's combined file is no longer in the tree (the zip sha256 above still identifies it). `validate_profiles.py`: "Checked 12 profiles / PASS".
- **Office skeletons** (template, disabled until after the feature freeze): `cfo`, `executive-assistant` and `chief-of-staff` name their services in `integrations.optional` (all `disabled` or `installed_disabled`) and forbid a SQLite backend, the platform env files and the production database. Services, database instance, rules and relay steps: [`office-skeletons/README.md`](office-skeletons/README.md). Demo story: [`compositions/EXEC-OFFICE-CROSSOVER.md`](compositions/EXEC-OFFICE-CROSSOVER.md) and `compositions/exec-office-crossover.json` (core = EA, overlays CFO and CoS, both disabled).
- **Glossary slices:** new scopes `finance`, `scheduling`, `governance` and `office` in `docs/glossary.yaml`, mapped from every module and source of the three profiles (and the CPA, gig-worker and chauffeur ledger modules), so each profile renders a real slice.
