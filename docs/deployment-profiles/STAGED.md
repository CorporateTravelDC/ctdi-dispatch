# Staged: parameterized deployment profiles (multi-vertical repurposing)

**Staged 2026-10-08 for later work. Not wired into anything.** Nothing in the platform reads these files; no loader, adapter, policy or test uses them yet. The pack's own README states the same: design-stage contracts, not deployable configuration.

## Provenance

- Received from the operator as `ctdi-parameterized-deployment-templates.zip` (sha256 `0738d34c2ab8834059f6e4a1d3a8967636b7cb92ffd5ce1ac59126a0e131f01c`).
- Files copied unchanged: `README.md`, `validate_profiles.py`, `schemas/profile.schema.json`, `profiles/*.json` (base plus eight verticals: chauffeur-regulated, executive-assistant-chief-of-staff, content-creator, independent-journalist, cfo, cmo, cpa, gig-worker).
- Reviewed on staging: placeholders only (`CHANGE_ME`, `example-*`, null coordinates); no secrets, credentials, personal data or real identifiers. `python3 validate_profiles.py` → "Checked 9 profiles / PASS: schema and security invariants".

## Notes for when the work starts (observations, nothing changed)

1. **Architecture default:** every profile sets `build.target_platforms` to `linux/amd64`; the reference deployment is `linux/arm64`, and builds pin the host platform (`docs/REPRODUCIBLE_BUILDS.md`, review 06). A loader should derive the platform from the host or require it explicitly.
2. **Closed objects:** `sources.credentials_ref`, `sources.source_regions` and `security.agent_scopes` are declared with no properties and `additionalProperties: false`, so the schema accepts only `{}` there. Real profiles need them to be open maps (credential references by name, per-agent scopes).
3. **Validator:** the strict schema check runs only when `jsonschema` is importable and passes silently otherwise; the structural checks always run.
4. **Overlap with existing work:** the README's gate 2 (geography provider) is the same set of hardcoded DC sets that security review 09 Test F and `docs/REGIONALIZATION.md` §2 list (SMES/surface airports, METAR stations, DC facility sets); gate 6 (absent receiver coordinates are an error) differs from today's behaviour (falls back to DC coordinates); gate 8 maps onto the signed-approval mechanism (`src/common/governance.py`) and the agent gateway's per-connector identity.
