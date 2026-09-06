# Documentation Refresh — 2026-08-11

A from-scratch reverification of this repo's documentation against the actual
running system: live `systemctl --user` / `podman ps` state, nginx and
cloudflared configuration, live HTTP probes of every public hostname, the
Ollama model registry, sysfs, and a line-level read of the relevant source
(`src/web/main.py`, `src/runner/main.py`, `src/auth/auth.py`,
`src/ingest/*`, `src/shared/watchlist.py`, `src/poller/main.py`,
`src/common/llm.py`, `build-models.sh`, every Quadlet). Nothing below was
taken on the old docs' word; every "actual" was independently confirmed on
2026-08-11.

Staleness ages are estimated from the docs' own "written/as of" language and
`git log` last-touch dates.

---

## Headline pattern

The single biggest source of drift was **docs that were accurate the day they
were written and never revisited after the system moved** — most dramatically
a hostname migration (2026-08-02) that left the README asserting *both* the
old and new state in different sections of the same file, and an LLM-stack
migration that three docs each captured at a different frozen moment. Median
staleness of a corrected claim: roughly 3–8 weeks. Two documents (SECURITY.md
and the LM Studio plan) were never true or long-moot, respectively.

---

## Fully rewritten

### `README.md` (780 → ~560 lines)
- **Self-contradiction on the public hostname, 7+ places.** The Status table
  (updated ~2026-08-03) correctly said `ops.example.com` was
  retired — while the API section, architecture diagram, runner section, and
  "Ops Dashboard" section (older strata) all still said *"`https://ops.example.com`
  now serves the full runner application"* and that
  `dispatch-runner.example.com` was *"retired as a live public
  endpoint… deliberately left unwired."* **Actual (probed live):** `ops.` is
  dead — one entry in `_RETIRED_HOSTNAMES` (`src/runner/main.py:271`),
  hard-404; `dispatch-runner.example.com` returns **200** and is
  the live, password-gated public demo (cloudflared → nginx →
  runner-demo :8005). Staleness: the two halves of the contradiction were 8
  days apart; the wrong half survived ~9 days.
- **Auth model.** Doc: *"Tier 1 → Tailscale-User-Login header |
  100.x.x.x source IP | cert bearer token"* and *"Tokens are created with
  `csex-token`."* **Actual:** `resolve_tier()` is purely bearer-token
  (`src/auth/auth.py` — the network-origin grant was removed as spoofable and
  the module docstring says so explicitly); `X-CTDI-Public: 1` from public
  vhosts pins Tier 0; the CLI is `ctdc-token`. Staleness: ~5 weeks
  (auth rework) / ~2 months (CLI rename).
- **LLM section.** Doc: *"mistral-nemo 12B"*, *"llama3.2:3b (chat)"*,
  *"OLLAMA_MAX_LOADED_MODELS=2, OLLAMA_KEEP_ALIVE=24h"*, model table of six
  generic registry models. **Actual (live `/api/tags` + Modelfiles):** 16
  dedicated `corporatetraveldc-pi5-*` models; brief class on `phi3:mini`
  (switched 2026-08-10/11 for the gemma3 SWA KV-cache bug), rest
  `gemma3:4b`; `OLLAMA_KEEP_ALIVE=10m`; neither mistral-nemo nor llama3.2 is
  even pulled. Staleness: ~2 months.
- **Watchlist.** Doc: *"loaded from YAML files"*, flights/trains only, POSTs
  to `/api/v1/watchlist` with tier-1 token. **Actual:** JSON files including
  `permanent_vessels.json`; typed admin-gated routes
  (`/api/v1/watchlist/{flights,trains,vessels}`). Staleness: ~2 months.
- **Route table.** Was ~40 routes; actual web surface is 96 registrations
  (67 handlers in `main.py` + 28 sub-router routes) — added the missing
  Tier-0 surface (FIDS, airspace, flightplan, wx-discussion, airmets, OSINT,
  board, sectors, aircraft registry, brief history…).
- **Runner RSS catalog.** Doc: *"Five built-in categories, three feeds each
  (15 total)"*. **Actual:** 11 categories / 27 feeds
  (`src/shared/rss_catalog.py`). Staleness: ~6 weeks.
- Removed: fabricated-sounding "Operational Topology / Key Security
  Safeguards" marketing block (its claims — e.g. compliance-hook egress as a
  live feature — were already retracted in `COMPLIANCE_SECURITY.md`'s
  2026-08-03 reframing note); stale FAA-SWIM-pending framing; `git clone`
  paths pointing at the pre-rename repo/location.

### `CLAUDE.md` (195 → ~200 lines)
- *"SR-1 Anthropic usage log"* → SR-1 is the skill usage log (LLM calls are
  local). *"Build all four images"* / four-container framing → current
  topology. Watchlist *"YAML files"* → JSON. `csex`-era command examples →
  `ctdc-token`, plus the real integrity-manifest re-signing requirement,
  the phi3:mini/SWA guard, `_abandon_ollama_generation()`, and the
  agent-operational conventions (stage-only git, notepad coordination,
  ET timestamps). Also corrected a claim inherited from `llm.py`'s own
  docstring: `ANTHROPIC_FALLBACK_ENABLED` is **not** set to false in
  dispatch.env — it defaults true; only per-call `allow_anthropic=False`
  blocks cloud fallback for the briefs.

### `docs/INFRA_MAP.md` (531 → ~250 lines)
- Doc (compiled 2026-08-02): *"20 containers running"*, *"**Only 2 of up to 7
  possible SWIM-feed ingest containers are currently running.** … today only
  NOTAM/FNS credentials are live"*, *"FAA SWIM/NMS credentials are still
  pending"*, Nextcloud *"at cloud.example.com"*, no mention of
  the four aggregator feeders, mcp hostname, or dav. **Actual:** 31
  containers at snapshot; **all 7 ingest containers running, all six SWIM
  feeds live** (and had been provisioned since 2026-07-20 — the "credentials
  pending" claim appears to have been wrong even when written); full
  hostname inventory added with live-probe results; the long §9 MCP
  git-archaeology narrative (resolved 2026-08-03) moved out to git history.
  Staleness: 9 days, portions wrong-at-birth.

### `docs/PI5-BOOT-CONFIG.md` (94 → ~120 lines)
- Doc assumed **Raspberry Pi OS**: `/boot/firmware/config.txt`, SD-card
  failure domains, `dtparam=nvme` + `dtparam=pciex1_gen=3` as required
  flags, `rpi-eeprom-config` available. **Actual:** Fedora Linux 44
  (aarch64), config at **`/boot/config.txt`** (`os_prefix=/efi/`), NVMe-only
  (no SD card exists), neither dtparam present yet it boots, EEPROM tooling
  not installed (BOOT_ORDER=0xf416 kept but explicitly marked unverifiable
  from the running OS). Documented the 2026-08-10 gpio-fan overlay removal,
  the still-active `dtoverlay=argonone` leftover, and the pending-reboot
  phantom `gpio_fan` hwmon. Staleness: ~4 weeks since last touch; the Pi-OS
  framing dates to the doc's origin (~2 months).

### `src/ingest/README.md` (195 → ~190 lines)
- Doc (2026-07-20 morning snapshot): FDPS *"NOT fixed… FIXM 3.0 field
  mapping is a stub"*, TFMS *"TMI_FLIGHT_LIST/trackInformation/
  flightPlanInformation/APTC/GADV … stubbed"*, ITWS *"dispatcher/handlers
  not yet built"*, restart instructions for `systemctl --user restart
  corporatetraveldc-ingest`. **Actual:** FDPS FIXM 3.0 implemented the same
  day (2026-07-20 3 pm session — the README describes the morning); TFMS has
  all 8 `fiMessage` types + 11 `fltdMessage` types implemented (only
  `flightPlanInformation` remains a deliberately-unregistered stub); the
  unified `corporatetraveldc-ingest` unit **does not exist** — 7 per-feed
  Quadlets since 2026-07-26. Added the 2026-08-10 `flightPlanAmendment`
  content-hash dedup (`tfms:amendment:{id}:{route}`) and the corrected
  600 s FIDS thresholds. Staleness: 3 weeks (some of it hours).
- Correction to the refresh brief itself, found while verifying: the
  amendment handler's docstring cites a `_handle_track_approach` function
  that doesn't exist — the real mirrored pattern is
  `_handle_track_information` (trigger string `tfms_track_approach`).
  The new doc names the real function.

### `src/shared/watchlist_README.md` (138 → ~150 lines)
- Doc (2026-06-07): flights + trains only; `POST /api/v1/watchlist/flights`
  with generic admin framing; no vessels anywhere. **Actual:** three entry
  types including **vessel (MMSI)** — `permanent_vessels.json`,
  `VESSEL_SWEEP_INTERVAL = 300` (hardcoded constant in
  `src/poller/main.py`, not an env var), AISHub bbox sweep requiring
  `AIS_AISHUB_ID`, `vessel-alerts` topic. Documented a **live bug** found
  during verification: `watchlist_event_hit()` had no vessel branch, so
  vessel *position* events pushed to `train-alerts`; only add/remove
  reached `vessel-alerts`. Staleness: vessels landed 2026-07-21 → 3 weeks;
  file itself 2 months old. **Fixed 2026-08-11** (same day, after this
  refresh landed): `watchlist_event_hit()` now has an explicit `vessel`
  branch (`domain_topic="vessel-alerts"`, MMSI + `notes`-as-name body,
  `VSL` title prefix) instead of falling through to the flight/train
  if/else.

### `docs/dispatch-runner-design.md` (302 → ~180 lines)
- Doc (v2.0, 2026-06-14): *"Accessible at:
  https://dispatch-runner.example.com"* as the live ops URL (it
  is now the *demo* URL; the live runner is tailnet-only), route table of 12
  (actual: 31 method+path combos — missing demo login/status, chat,
  ACARS/VDL2/HFDL/AIS views, ntfy stream, config, resolve-source…), RSS
  *"Five built-in categories… 15 total"* (actual 11/27),
  `DISPATCH_BASE_URL: http://127.0.0.1:8000` (actual live value in the
  Quadlet: `http://100.x.x.x:8000`; demo instance points at :8004), auth
  described as bare 100.64.0.0/10 middleware (actual: `_is_trusted()` +
  demo HMAC cookie gate + Tier-1 token-injection allowlist). Staleness:
  ~2 months.

### `docs/SDR_SERVICES.md` (82 → ~90 lines) + `docs/SDR_SERVICES_README.md` (82 → 6)
- Doc: *"All SDR decode/watch services ship as `.disabled` Quadlets."*
  **Actual (live Quadlet dir):** ultrafeeder, acarsrouter, acarshub,
  dumpvdl2, acars-watcher, piaware, fr24feed, planefinder, airnavradar are
  enabled and running (ultrafeeder currently crash-looping on the missing
  ADS-B dongle); only acarsdec/dumphfdl/ais/ais-watcher remain `.disabled`.
  The two files were byte-identical duplicates — `SDR_SERVICES_README.md` is
  now a pointer stub. Staleness: ~2 months.

### `SECURITY.md` (21 → ~40 lines)
- Was untouched GitHub template boilerplate — a fictional *"5.1.x ✅ /
  5.0.x ❌"* version-support table and placeholder reporting text, in place
  since 2026-06-08 (never true). Replaced with the real policy: single
  continuously-deployed main, GPG fingerprints, signed-manifest integrity
  system, secrets/token handling, CUI reporting.

---

## Corrected in place (targeted edits)

### `docs/DEDICATED_MODELS_PLAN.md`
- Added a dated status block: *"15 models"* → **16** (adds
  `disruption-weather-digest`, among others); *"13 distinct call sites"* →
  **24 call sites across 19 files** (the census predated
  `disruption_weather_digest.py` and the six category-watch skills sharing
  `-aam-watch`); brief models *implied gemma3:4b* → **`FROM phi3:mini`**
  with the `SWA_DENYLIST_REGEX` guard + smoke-test promotion gate and the
  `keep_alive:0` orphan-generation fix. §2–§5 (mini-RAG, fine-tuning, EA
  variant) confirmed still designed-not-built. Staleness: 9 days.

### `docs/lmstudio-dispatch-prompts.md`
- Marked **[RETIRED]** with a banner (kept as historical record rather than
  deleted): its entire premise — planning a *future* Anthropic→local
  migration via **LM Studio**, with `claude-haiku-4-5` listed as each
  skill's "current model" — is moot; the migration happened via Ollama and
  is superseded by `DEDICATED_MODELS_PLAN.md`. Staleness: ~2 months
  (premise dead for at least 5–6 weeks).

### `docs/auth-token-proxy-pattern.md`
- Tier table said Tier 0 = *"Anyone on Tailscale"* — actual: anyone,
  tier resolution is token-only; added the `X-CTDI-Public` pin and the real
  removed-network-grant history. Exec examples ran
  `python3 /app/ctdc_token/cli.py` inside the web container — actual
  in-container path is **`/app/src/ctdc_token/cli.py`** (Containerfile.web:
  `WORKDIR /app`, `COPY src/ src/`), fixed in 3 places. Runner gate
  description updated to `_is_trusted()` reality.

### `docs/regulated-operator-setup.md`
- *"Rotate via `csex-token rotate`"* — doubly wrong: the CLI is
  `ctdc-token`, and no `rotate` subcommand exists (verified against
  `src/ctdc_token/cli.py`; rotation = revoke + create). Staleness: ~6 weeks
  (the `csex-` prefix was retired with the naming convention change).

### `docs/REGIONALIZATION.md`
- Section head *"`src/ingest/config.py` — DC static airspace"* pointing at
  *"`src/common/airspace_static.py`"* — neither is the airspace file.
  **Actual:** `src/geo/dc_airspace.py` (P-56A/B, FRZ, SFRA; served by
  `/api/v1/airspace`). Staleness: ~1 month.

### `docs/DATA_SOURCES.md`
- NOTAM credentials block listed only `FAA_NOTAM_API_KEY=` — the fetcher
  (`src/poller/fetchers/notam.py`) requires **`FAA_NOTAM_API_SECRET`** too
  and stays `awaiting_credentials` without both. Added it + snapshot header.

### `docs/DCA_IAD_FIDS.md`
- *"registered in FETCH_SCHEDULE at 60s interval … Stale threshold: 180s"*
  — actual: **300 s** interval (`src/poller/main.py:55-56`) and **600 s**
  threshold (raised 2026-08-10; the 180 s value was tighter than the real
  poll interval and manufactured false "stale" states ~40% of every cycle).

### `docs/ALERT_REFERENCE.md`
- Added a 2026-08-11 addendum: the 2026-07-27 per-parser catalog predates
  the escalating family-alert rollout (`tfms-*`/`tbfm-*`/`fdps-*`/`itws-*`/
  `aim_fns-*` zone topics, per-topic throttle/enable/sanitize, sectors admin
  API), the vessel-alerts topic (and its position-event misroute bug), the
  2026-08-10 TFMS amendment content-hash dedup, and brief-fallback-monitor.
  Staleness of missing content: ~9 days.

### `docs/REFERENCE_INFRA.md` (public counterpart of INFRA_MAP)
- Ingest row updated to the 7-container split; MCP tool count 21 → 34 (the
  doc already said 34 elsewhere — internally inconsistent); T1 wording
  de-Tailscaled; watchlist noted as JSON incl. vessels; runner exposure
  wording aligned with the demo/tailnet split.

### `docs/SECOND_BRAIN_STATUS.md`
- Prepended a dated update: vault now lives under the dedicated
  `corporatetraveldc` Nextcloud account; **`dav.example.com`**
  is the interactive host, **`cloud.example.com`** is the
  locked-down automation-only vault endpoint (2026-08-08 design); the
  "index DB not auto-refreshing" gap is closed by the 04:00 index-scan
  timer. Older sections left intact as history per the doc's living-log
  convention. Staleness: 3 days.

### Small fixes
- `docs/DESIGN-PRINCIPLES.md` — cloud-inference opt-in gate lives in
  `src/common/llm.py` (keyed on `ANTHROPIC_API_KEY`), not
  `src/runner/main.py`.
- `docs/executive_summary.md` — reverification note (claims held up).
- `docs/HEADLESS_ACCESS.md` — verified-current stamp (tailscaled active,
  both tags advertised, Pi Connect still absent — all re-checked live).
- `docs/tasks/scheduled/README.md` — noted only `flight-hifi-track` ships in
  this repo's `skills/`; the other two are Cowork-side.

---

## Verified, deliberately unchanged

- `docs/COMPLIANCE_SECURITY.md`, `docs/HONEYPOT_FAIL2BAN.md`,
  `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md` (all reworked 2026-08-09),
  `docs/HARDWARE_GUIDANCE.md` (2026-08-06), `docs/COST_STRUCTURE.md`
  (2026-08-07), `docs/ISO_42001_ALIGNMENT.md`,
  `docs/GUARDRAILS_JUSTIFICATION.md` — spot-checked key claims (thermal
  governor points, CPUWeight 500, honeypot mechanism, cost framing); no
  corrections needed.
- Dated historical artifacts left as records: `docs/SUDO_JUSTIFICATION_PROPOSAL.md`
  (its one `csex-token` mention is *inside* a passage explicitly flagging it
  as stale), `docs/TAILNET_MIGRATION_INVENTORY.md` (migration executed
  2026-08-05, self-annotated), `docs/INVESTOR_MATERIALS_REVERIFICATION_2026-08-09.md`,
  `docs/benchmarks/*`, `docs/tickets/*`, `docs/tasks/scheduled/ua925-*` /
  `nec-train-hifi-track.md`.
- `addenda/README.md`, `addenda/wpc_forecast_discussions/README.md`
  (WPC products confirmed live — `/api/v1/wx/discussion` routes exist),
  `scripts/pre-commit-README.md` (pattern table matches the hook script
  exactly).

---

## Live-system findings surfaced by this audit (not doc fixes — operator action items)

1. **`corporatetraveldc-mcpo.service` (the full/admin 34-tool instance)
   was listening on `0.0.0.0:8082`** — its deployed unit had no `--host`
   flag — while both its own comments and the mcp vhost describe it as
   "loopback-only by design." The public :8083 instance was correctly
   127.0.0.1-bound. **Fixed 2026-08-11**: the tracked repo copy of the
   unit already had `--host 127.0.0.1` (added 2026-08-06) but was never
   redeployed live — the same "written, never shipped" pattern as the
   mcpo-public/:8083 deployment gap (see `docs/INFRA_MAP.md` §9 for that
   full story). Synced the live unit to the repo copy, restarted, confirmed
   `ss -tlnp` now shows `127.0.0.1:8082` only.
2. **Vessel position alerts misrouted to `train-alerts`**
   (`watchlist_event_hit()` lacked a vessel branch; `EntryType` literal
   never updated). **Fixed 2026-08-11**: explicit `vessel` branch added
   (`vessel-alerts` topic, `VSL` title prefix), `EntryType` literal now
   includes `"vessel"`.
3. **`cloud.example.com` is live by design, not stale**: the
   2026-08-08 nginx vhost deliberately keeps it as the automation-only
   vault endpoint (`webdav_client.py` sends its Host header), while `dav.`
   is the interactive host. If full retirement in favor of `dav.` is
   intended, cleanup spans the tunnel ingress (dashboard-managed), the
   vhost (+ stale `.conf.bak-20260807`), and `webdav_client.py`.
   `cockpit.example.com` by contrast is fully dead (no ingress,
   no vhost, no DNS answer) and now appears in no doc as live.
4. **`thermal-samples.csv` gets a duplicate header row every 5 minutes**
   (`thermal-sample.sh`'s schema-transition check matches against line 1,
   which is still the old header) — ~130 stray header rows so far.
5. **UltraFeeder was down at this refresh's original snapshot** (ADS-B
   dongle absent from the USB bus; unit crash-looping on
   `/dev/rtl_sdr_adsb`); the adsb-feed-silence-watchdog alerted correctly
   throughout. **Restored midday 2026-08-11** after a physical reseat —
   container up since 12:31 EDT, both RTL dongles enumerating, live decode
   confirmed.
6. Cosmetic `csex-token` remnants in non-doc files: `build-images.sh:66`,
   `dispatch-secrets.env.template:202-204`.
7. **`corporatetraveldc-pi5-disruption-weather-digest` was never actually
   built** — found during the 2026-08-11 sanity pass, still open. The
   Modelfile and `build-models.sh`'s `MODELS` map both list it (16 models
   total on paper), but it's absent from Ollama's live registry
   (`/api/show` → not found); today's 04:35 run 404'd on `/api/generate`
   and silently fell back to raw data. Likely never built after the commit
   that added it. No corresponding `ollama-prewarm-disruption-weather-digest`
   timer exists either (every other dedicated-model skill has one) — check
   whether that's intentional or the same gap. **Action**: re-sign the
   manifest, then `./build-models.sh corporatetraveldc-pi5-disruption-weather-digest`.
7. `src/common/llm.py`'s docstring claims dispatch.env sets
   `ANTHROPIC_FALLBACK_ENABLED=false` — it doesn't (defaults true; the
   briefs are protected per-call instead).
