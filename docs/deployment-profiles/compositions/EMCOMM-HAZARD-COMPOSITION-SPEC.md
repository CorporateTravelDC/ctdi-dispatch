# Spec: hazard / EMCOMM-CERT deployment composition (staged 2026-10-10)

**Status:** ~~spec only.~~ (corrected 2026-10-15: the §3 skeleton shipped on 2026-10-10 as `src/common/composition.py`, `tests/common/test_composition.py` and `emcomm-cert-hazard-operations.json`; see §3.) **Not wired, not deployed, disabled by default.** Source: the ChatGPT design package posted to the coordination board on 2026-10-09. The board messages are the verbatim record:

| Artifact | Board id |
|---|---|
| Announcement | `brd-ab484d154036` |
| 1/3 `deployment-profile.template.yaml` (a `DeploymentComposition`) | `brd-16924fa4307d` |
| 2/3 `ARCHITECTURE-AND-ROLLOUT.md`, parts A and B | `brd-d81dde4b91db`, `brd-34824aad28d6` |
| 3/3 the implementation prompt | `brd-551011e77ad5` |
| Earlier, related: MCOM emergency-response profile draft (vault contribution) | `brd-68bd0122494a` |

This is the first **composition**: one core profile plus overlays. The eight earlier vertical profiles (`../profiles/`) are single profiles. This spec maps the proposal onto what the repo actually has and defines the smallest core to build first.

## 1. Crosswalk: proposal vs. repo (verified 2026-10-10)

| Proposal assumes | Repo reality | Consequence |
|---|---|---|
| A `core_profile: base-dispatch` | `src/common/deployment_profile.py`: a typed loader, one profile per process, with the reference profile as the default | The **compiler** route: compositions compile to one effective profile, so the runtime needs no change (the proposal's own fallback) |
| An NWWS-OI connector | Live: `src/ingest/nwws.py`, with VTEC phenomenon/significance mapping | Inherited as is |
| An FAA ITWS connector | Live via SWIM (`ingest-itws`) | Inherited; terminal **context**, never a public warning |
| NHC advisories, USGS events | **Absent** (0 references in src) | Proposed-only feed adapters, each a separate approval; not part of the core |
| HEARS / HEART / SHARES | SHARES is an auth tier (`src/auth/auth.py`, Tier 2). Credentialed radio frequencies ~~live only in `~/.secrets` (`docs/regulated-operator-setup.md`)~~ never enter code, configs, exports, ntfy bodies or the repo; the shipped configs are placeholders (`docs/regulated-operator-setup.md` "CUI / FOUO handling", corrected 2026-10-15). No HEARS/HEART runtime | Preserve as is. The composition may only **reference** tiers and never carry frequencies or credentials |
| EMCOMM / CERT roles | No runtime concept (CERT hits in src are TLS certificates) | The overlay stays `disabled`. Roles have to be designed (accounts/groups) before any enablement |
| MCOM | Not defined anywhere in the repo | The operator defines it before the MCOM draft is mapped |
| ACARS | Out of scope by the proposal's own terms | Unchanged |
| Outward delivery, radio TX | ntfy topics and the push-alert API exist; no radio TX | The composition forces `outward_delivery: disabled` and `radio_tx: disabled`. Validation rejects anything else until a separate approval |

## 2. Design (what gets built)

- **Composition file:** `compositions/<id>.json`, JSON like the existing profiles (the YAML in the proposal is illustrative). Fields:
  - `id`, `version`, `core` (a profile id plus its SHA-256 pin);
  - `overlays[]`, each with `id`, `version`, `when` and `enabled`;
  - `audience_policy`, `protections`, `event_policy`, `escalation`, `gates`;
  - `state`, one of: `discovered → mapped → validated → staged → shadow → pilot → active → suspended/retired`.
- **Compiler:** `common/composition.py`, standard library only.
  - `compile(composition) -> effective profile dict` plus a **dry-run diff** against the current effective profile. No I/O side effects.
  - Precedence: core < overlays in listed order < explicit composition fields. A conflicting key across two overlays is an **error**, never last-wins.
- **Rejected at validation:**
  - an unknown overlay;
  - an overlay with a missing dependency;
  - any permission or audience **expansion** beyond the core;
  - `outward_delivery` or `radio_tx` not `disabled`;
  - a `state` past `shadow` without a signed activation record;
  - a core whose SHA-256 doesn't match the pin.
- **Activation is a separate record**, never inferred from a file being present: signed (the existing governance approvals), carrying environment, composition hash, state and a rollback pointer. Rolling back means re-pointing to the previous signed record; templates are never edited.
- **Event policy:**
  - the agency-issued product is authoritative (NWS warning > ITWS context);
  - correlation key `(agency, agency_event_id, revision)`;
  - one notification per audience per event revision;
  - updates, cancellations and expiries are honoured;
  - stale or uncertain input goes to operator review only;
  - season can raise review cadence but never triggers an alert by itself.

## 3. Skeleton core (first build slice, after the 2026-10-10 token rotation)

> **Shipped 2026-10-10 (checked 2026-10-15 at main 1491bf5a).** Differences from the plan below:
> - `composition.py` has no dataclasses. It works on plain dicts: `load()`, `validate()`, `compile()`, `diff()`
>   and a `CompositionError`.
> - The example file has five overlays and **all five** are `enabled: false`: `weather-alerting` (NWWS),
>   `aviation-terminal-context` (ITWS), and `tropical-cyclone`, `earthquake-impact`, `emcomm-cert` with a
>   `status: proposed ...` note. There is no top-level `enabled` key; `state` is `mapped`, and
>   `escalation` holds `outward_delivery: disabled`, `radio_tx: disabled` and
>   `thresholds: UNSET_VERIFY_WITH_OPERATOR`.
> - Of the §2 validation rules, these are implemented: state check, any state past `shadow` refused (there is
>   no activation record yet, so `pilot`/`active` are always refused), `outward_delivery`/`radio_tx` must be
>   `disabled` (any other value is refused; a missing key counts as disabled), overlay key conflicts and audience
>   expansion beyond the core (checked on enabled overlays only), and the core SHA-256 pin (checked in `load()`; a composition with **no** pin is
>   accepted without a check). **Not yet
>   implemented:** rejecting an unknown overlay, rejecting a missing overlay dependency, and permission (as
>   opposed to audience) expansion.
> - Tests: 7 test functions, covering the list below (the "dry-run diff is empty" case is the example
>   compiling to the core unchanged).

1. `src/common/composition.py`: the schema dataclasses, `load()`, `validate()`, `compile()` and `diff()`. The reference profile is the only core.
2. `docs/deployment-profiles/compositions/emcomm-cert-hazard-operations.json`: the proposal mapped onto the crosswalk. `enabled: false`, `state: mapped`, NWWS and ITWS overlays only; the NHC/USGS/EMCOMM overlays are present but `enabled: false`, marked `proposed`.
3. `tests/common/test_composition.py`:
   - deterministic compile;
   - an overlay conflict is an error;
   - permission expansion is rejected;
   - outward delivery or radio TX is rejected;
   - a state past shadow without an activation record is rejected;
   - the core hash pin is enforced;
   - the dry-run diff is empty for the reference-only composition.
4. **No runtime wiring.** Nothing reads compositions until a later approved slice, so behaviour stays unchanged.

## 4. Later slices (each needs its own approval)

1. Template inventory (proposal Phase 0): `docs/deployment-template-inventory.md`, covering the 8 verticals, the base and the reference, all with `loaded ≠ deployed` shown.
2. Correlation and normalization in shadow, against recorded NWWS/ITWS traffic (no-send).
3. Activation records plus the signed state machine.
4. NHC and USGS adapters: separate proposals.
5. EMCOMM/CERT roles, after the operator defines them, along with MCOM.

## 5. Open for the operator

- The MCOM definition.
- Whether EMCOMM/CERT participants are platform accounts (a group) or only an audience list.
- The escalation thresholds (the proposal leaves them `UNSET_VERIFY_WITH_OPERATOR`). These tie into the stack-wide escalation and de-escalation thresholds item on the backlog.
