# Full Rust Rewrite Assessment — ctdi-dispatch-internal

Exploratory analysis only. Scope: everything under `ctdi-dispatch-internal`
as a ground-up Rust rewrite — a real port, not a Python core with a Rust
wrapper (and not the reverse). No FFI shim retained as a permanent crutch.
Target hardware unchanged: a single Raspberry Pi 5 (aarch64, 8 GB), podman,
SQLite, local Ollama, Nextcloud/WebDAV, ntfy, Pushover, Cloudflare tunnel.

Bottom line up front: the ingest layer (SWIM Solace + XML parsing + geo
filter + dedup) is where Rust pays for itself — it is long-running, native,
memory-lifetime-sensitive code that today only catches its failure modes at
runtime. The skill/LLM/orchestration layer is where Rust costs the most and
buys the least. A full rewrite is feasible and would harden the platform,
but it is a multi-month effort whose value is very unevenly distributed; the
honest recommendation is a staged port that starts with ingest and stops to
re-evaluate before touching the skills layer, not a big-bang rewrite.

---

## 1. Module-by-module rewrite plan and order

Order is chosen so each stage is independently shippable, testable against
the live feeds/DB, and reversible (the Python service it replaces can run in
parallel behind the same SQLite until the Rust one is trusted).

**Stage 0 — shared foundation crate (`ctdc-core`).**
Config loading (env + dispatch-secrets.env), the SQLite access layer, the
audit-log writer, `content_hash`, and the error taxonomy. Everything else
depends on this. Crates: `rusqlite` (bundled SQLite), `serde`/`serde_json`,
`sha2`/`md-5`, `time`, `tracing` for logging. This is also where the
Python `common/config.py`, `common/db.py`, and `common/sr1_log.py`
equivalents land.

**Stage 1 — the poller fetchers (`ctdc-poller`).**
Lowest risk, highest surface: ~30 HTTP fetchers (metar, notam, nws, tfr,
fids, amtrak, faa_registry, eurocontrol, jasdat, opensky, ...). Each is a
pure `reqwest` GET + parse + DB upsert. No shared mutable state, no
lifetimes to fight. Port fetcher-by-fetcher; run the Rust poller against a
scratch DB and diff its writes against the Python poller's for a week.
`reqwest` + `tokio` + `quick-xml`/`serde_json` cover all of them.

**Stage 2 — the SWIM ingest layer (`ctdc-ingest`).** The main event; see §2.
Per-feed binaries (fdps/tfms/tbfm/stdds/itws/notam/aim) sharing one crate,
matching today's 7-container split. This is where Rust's guarantees matter.

**Stage 3 — pusher + shared dedup/throttle (`ctdc-pusher`, `ctdc-dedup`).**
Port `push_dedup.py` (now flock+mtime-merge — trivial and *more natural* in
Rust, see §3), the sector-coalesce/family-alert throttle, ntfy + Pushover
senders, VIP TFR / landing / wx-change loops. Small, well-bounded, lots of
integer/timestamp arithmetic that Rust makes safe.

**Stage 4 — the web API (`ctdc-web`).**
`axum` (same tokio/tower stack as reqwest) replacing FastAPI: auth tiers,
watchlist routes, vault endpoints, SSE, the transparent dispatch proxy.
Straightforward but voluminous; the auth model (X-CTDI-Public → Tier 0
before token lookup) ports cleanly and becomes a typed middleware.

**Stage 5 — skills, LLM, second-brain (`ctdc-skills`).**
The hardest to justify (see §5 cons): ~40 skills that are mostly "pull rows,
build a prompt, call Ollama, write a narrative + fire ntfy." Rust's win here
is near zero (the work is I/O and string templating) and its cost is real
(no Jinja-ish ergonomics, more ceremony per skill). Port last, or never —
this is the natural seam to leave in Python behind a subprocess/HTTP boundary
if a full rewrite proves not worth it.

**Stage 6 — retire the Python tree** only once Stages 1–4 have run in
production long enough to trust. Keep the guardrails (scrub gate, dedup,
audit) as the last things cut over, never the first.

---

## 2. The SWIM ingest parsers (the reason to do this at all)

Today (`src/ingest/swim_client.py` + `parsers/*.py`): a Solace `receive_message`
loop, `msg.get_payload_as_bytes()`, `ET.fromstring`, namespace-prefix
guessing, per-feed handler dispatch, geo-filter, PushDedup, DB write, then a
client-`ack`. The whole thing is defensive against its own runtime surprises
— note the volume of "confirmed against a real capture" / "unconfirmed tag
shape" / "fall back to auto-ack" comments. That defensiveness *is* the cost
of doing this in Python: the shapes are only known at runtime, so the code is
a thicket of `.get()`-with-default, broad `except Exception`, and
truthiness guards (see the FDPS element-truthiness test — an `Element` is
falsy when it has no children, a real bug class this codebase hit).

A Rust port changes the failure surface:

- **XML.** `quick-xml` (streaming, pull-based) or `serde-xml-rs`/`yaserde`
  (derive structs). The pragmatic choice for these FAA schemas is `quick-xml`
  in streaming mode with hand-written matchers, because the messages are
  large, batched (a single asdexMsg carries up to ~51 positionReports), and
  namespace-noisy — deriving exhaustive structs for FDPS/TFMS/TBFM/STDDS is
  more work than the streaming state machine and less tolerant of the "same
  msgType, two document shapes" reality (fiOutput vs fltdOutput). Streaming
  also caps memory: the current code holds the full `ElementTree` in memory
  per message; `quick-xml` lets you extract-and-drop.
- **Namespaces.** Today's `_local_tag`/prefix-guessing becomes explicit:
  `quick-xml` gives you the raw qualified name and you match on local-name
  deliberately. The "broadened to case-insensitive/substring matching, still
  unconfirmed" TBFM situation becomes a typed, tested matcher instead of a
  hopeful string contains.
- **The Solace client is the real risk (see §5).** There is no mature native
  Rust Solace SMF client. Options: (a) FFI to the Solace C API (`solclient`)
  via `bindgen` — viable, the C lib is aarch64-supported, but you own the
  unsafe boundary and the same manual message-lifetime/ack discipline the
  Python code just fought through (the auto-ack-vs-client-ack crash saga);
  (b) if any feed is reachable over AMQP 1.0/MQTT, use a native Rust client
  for those and FFI only where SMF is mandatory. This decision gates the
  whole ingest stage and must be settled first with a spike.
- **Backlog fast-forward triage** (the age-based drop on reconnect) is pure
  timestamp arithmetic and ports directly — and gets *safer*, because the
  `sender_ts_ms is None` fall-open path becomes an explicit `Option` match
  the compiler forces you to handle.
- **Geo filter** (`parsers/geo_filter.py`, `geo/dc_airspace.py`,
  bounding-box + haversine) is textbook Rust: `f64` math, no allocation, and
  the DCA-radius/Marine-One checks become total functions. The haversine
  symmetry test carries over verbatim.

Net: the parsers are the part of the platform whose bugs are silent (dropped
records, collapsed dedup slots, falsy-Element skips, wrong-tz timestamps) and
whose correctness is currently asserted by comments and a handful of
fixtures. That is exactly the profile Rust's type system converts from
runtime-hope to compile-time-guarantee.

---

## 3. Rust-vs-Python gotchas (concrete, both directions)

### What Rust catches at compile time that this codebase only catches (or misses) at runtime

- **The dedup key/content_key swap.** The calibration bug — `should_push`
  called with a literal constant as the stable `key` and the per-entity hash
  as `content_key` — is two `String`s in Python, indistinguishable. In Rust
  make them distinct newtypes: `should_push(slot: DedupSlot, content:
  ContentKey)`. Passing `ContentKey` where `DedupSlot` is expected is a type
  error. Every one of the 6 collapsed call sites would have failed to
  compile. This single pattern justifies the newtype discipline platform-wide
  (TfrId, Callsign, Hex, FacilityId, Gufi — today all bare `str`, freely and
  wrongly interchangeable; recall flight_events.flight_id is a GUFI but is
  matched against tbfm callsigns elsewhere).
- **The `hot=True` bypass.** A `bool` positional flag that silently disables
  the whole mechanism. In Rust model it as `enum Urgency { Normal, Hot }` (or
  don't give the function the flag at all and expose a separate `force_push`)
  — a caller can't "accidentally pass True because the alert feels
  important"; the type makes the bypass explicit and greppable.
- **Naive-vs-aware datetime.** `now(aware) > parse(naive)` raising TypeError
  and aborting the whole sweep is impossible in Rust: `time::OffsetDateTime`
  and `PrimitiveDateTime` are different types; you cannot compare them, and
  parsing a zone-less string yields the primitive type, forcing an explicit
  "assume UTC / reject" decision at the call site. The America/New_York-vs-UTC
  discipline (a standing operator directive) becomes a `Tz`-typed value, not
  a convention you have to remember.
- **Falsy Element.** `if not element:` being true for a childless-but-present
  node (a real FDPS bug this repo has a regression test for) does not exist:
  Rust has no truthiness; you match `Option`/`Result` explicitly.
- **The `.get('avg_delay_minutes', '?')` class.** A missing dict key silently
  defaulting to a display string that then ships to users ("+?min") is a
  `HashMap::get -> Option` you must handle; better, model the program as an
  `enum Program { GroundDelay{avg_delay:u16}, Mit{nm:u16}, Minit{min:u16},
  Apreq, Stop }` so "a MIT has no avg delay" is unrepresentable, not a
  runtime `?`.
- **State-file races.** The PushDedup cross-process clobbering (whole-dict
  rewrite of stale cached state) is a data race the Python type system says
  nothing about. In Rust the shared-file access is a typed resource guarded
  by an explicit lock; "read once, cache forever, overwrite peers" is not the
  path of least resistance the way it was in Python.
- **Resource leaks.** The two leaked `sqlite3.connect` handles on the
  exception path (entity_tracking) cannot happen: `rusqlite::Connection` is
  `Drop`-closed at scope exit, no try/finally required.
- **Broad `except Exception`.** The pervasive catch-all-and-log that masks
  bugs (a handler error silently drops a message and skips its counters)
  becomes typed `Result<_, IngestError>` with explicit variants; the compiler
  makes you decide per error whether it's droppable or fatal.

### What Python gives for free that a Rust rewrite must answer for (not hand-wave)

- **The Solace SMF client.** No mature pure-Rust SMF client exists; Python
  has the vendor SDK. This is the single biggest real cost — see §2/§5. A
  rewrite must commit to FFI-to-`solclient` (and own the unsafe message
  lifetime/ack boundary, the exact thing that caused the auto-ack crash saga)
  or prove AMQP/MQTT reachability. Not optional; gates the project.
- **Dynamic XML tolerance.** Python's `ElementTree` + `.get()` shrug at
  unexpected shapes, which is *why* the parsers survived unconfirmed schemas.
  Rust makes you enumerate shapes up front. That is the whole point (safety)
  but it front-loads work and means the "capture a real sample, then broaden
  the matcher" iteration loop has a slower turn (recompile vs edit-and-run).
- **LLM/prompt ergonomics.** The skills build prompts with f-strings and
  loose dict inputs and call Ollama over HTTP. Rust has no equally terse
  templating; `serde` round-trips are safer but wordier. There is no Rust
  advantage in this layer — it's I/O-bound string work — so the rewrite pays
  ergonomic cost for no safety gain (why §1 ports it last or leaves it).
- **Iteration speed / operator familiarity.** Python edits are live on the Pi
  with a container restart; Rust needs a cross-compile or on-Pi build
  (slow on aarch64 — see §5). Hotfixing a parser at 2am against a live feed
  anomaly is materially harder in Rust. This is a real operational
  regression for a single-operator platform.
- **`None`/optional sprawl.** Much of the schema is legitimately optional
  (gate, terminal, avg_delay, eta). Rust models this correctly with `Option`,
  but the ergonomic tax (`?`, `unwrap_or`, matches) is nontrivial across
  thousands of optional fields — honest, ongoing verbosity, not a one-time
  cost.
- **Duck-typed reuse.** Helpers like `_fcm_text(elem, tag)` work across
  every schema by duck typing. In Rust you either write per-schema accessors
  or lean on a generic streaming reader; either way it's more code than the
  one Python helper that serves all feeds today.

---

## 4. Reimplementing the guardrails

The platform's guarantees must survive the port. All four already write to
the same SQLite audit trail, which stays the shared contract between Rust and
any not-yet-ported Python during the staged migration.

- **Scrub gate (CUI/PII block-not-redact).** `second_brain/scrub_gate.py`'s
  `gate()` is pattern matching over text that BLOCKS (raises) on CUI radio
  shapes / SSN-like tokens rather than redacting. Port as
  `fn gate(text, source) -> Result<&str, ScrubGateBlocked>` using `regex`
  (or `aho-corasick` for the fixed CUI markers). The block-not-redact
  contract is a `Result`, and — critically — the discipline of "if you write
  to the vault directly you must gate first" becomes enforceable: make the
  WebDAV `put` for served/ingested content take a `Gated<String>` newtype
  that only `gate()` can construct, so an ungated write won't compile. That
  is strictly stronger than today's convention.
- **Dedup / throttle.** `push_dedup.py` (post-fix: mtime-reload + flock merge)
  ports naturally — `fs2`/`fd-lock` for the advisory lock, `serde_json` for
  the state, and the typed slot/content keys from §3. The family-alert /
  sector-coalesce escalation + per-topic throttle is timestamp + counter
  arithmetic that Rust makes overflow-safe. Consider promoting the state from
  a JSON file to a SQLite table during the port (removes the file-lock
  dance entirely and gets ACID for free, since SQLite is already the shared
  store).
- **Audit log.** `db.audit(...)` → a single `audit(action, kind, ...)` fn in
  `ctdc-core` writing the same rows. Because it's the migration contract,
  port it in Stage 0 and have both runtimes write to it.
- **SR1 (mutation gate) / SR2 (model routing) — `guardrails.py`.** These are
  currently dormant (no live callers) and pure decision functions. Port them
  as total functions returning typed decisions (`enum GateDecision`,
  `enum Tier`), which is a strictly better home for them than the current
  string-keyed dict returns — SR2's task→tier map and budget/token thresholds
  become exhaustive `match`es the compiler checks. Low effort, and a good
  early proof-of-life for the `ctdc-core` types.

---

## 5. Honest cons

- **Pi 5 build/deploy.** aarch64 Rust builds are slow; a full workspace clean
  build on the Pi is impractical. You need cross-compilation from a dev host
  (or `cross`/QEMU) and a disciplined CI-to-Pi artifact flow. That's a new
  piece of infrastructure this single-operator, edit-live-on-the-box platform
  doesn't have today. Incremental rebuilds are fine; cold builds and
  dependency bumps are painful. Binary size and the loss of "just edit the
  .py in the container" are real day-to-day regressions.
- **Solace / SWIM client maturity.** Restated because it's decisive: no
  production-grade pure-Rust SMF client. FFI to `solclient` is the realistic
  path and re-introduces exactly the manual buffer-lifetime/ack hazard the
  Python client-ack rollout was built to tame — except now in `unsafe` Rust.
  This is the highest-risk single item and should be de-risked with a
  throwaway spike *before* committing to the rewrite.
- **XML ecosystem.** `quick-xml` is solid and fast but low-level; there is no
  `ElementTree`-grade batteries-included tree API with the same forgiving
  ergonomics. Deriving structs (`yaserde`/`serde-xml-rs`) is nicer to read
  but brittle against the "one msgType, several document shapes" and
  namespace-prefix variance these FAA feeds actually exhibit. Expect to
  hand-write streaming matchers and carry a fixture corpus.
- **WebDAV.** No first-class Rust WebDAV client worth adopting; you'd build
  the handful of verbs used (`GET`/`PUT`/`PROPFIND`/`MKCOL`) directly on
  `reqwest` with the custom `Host` header and app-password auth — which is
  essentially what `webdav_client.py` already is, so this is low-risk but
  it's bespoke code you own, not a library.
- **SQLite.** This is a *pro*, not a con: `rusqlite` (bundled) is mature and
  a strict improvement — compile-checked-ish queries, `Drop`-closed
  connections (kills the leak class), real transactions. The one caveat is
  the concurrent-writer reality (7 ingest processes + poller + pusher + web
  on one file): keep WAL mode and the busy-timeout the Python side relies on;
  Rust doesn't change SQLite's single-writer semantics.
- **HTTP.** `axum`/`reqwest`/`tokio` is production-grade and a genuine win for
  the web + fetcher layers. FastAPI's automatic request parsing/validation is
  replaced by `serde` extractors — comparable ergonomics, better guarantees.
  SSE (`live_events`) and the transparent proxy both have clean `axum`
  equivalents. Lowest-risk large chunk of the rewrite.
- **LLM layer.** No Rust benefit; Ollama is HTTP so `reqwest` reaches it
  fine, but prompt templating and the loose narrative-building are more
  verbose and no safer. This is the clearest "leave in Python" candidate.
- **Total cost & risk concentration.** The effort is large and its payoff is
  concentrated in ingest + dedup + geo (Stages 2–3). Stages 4–5 are big and
  low-yield. A big-bang rewrite risks spending the majority of the effort
  (skills/web volume) for the minority of the safety benefit, on a platform
  that is currently working. The rational shape is: spike the Solace
  question, port Stage 0–3, run in parallel against live feeds, then decide
  whether Stages 4–5 are worth it or whether Python-behind-a-boundary is the
  right permanent split.

---

## 6. Recommendation

Do it staged, ingest-first, and gate the whole decision on a Solace-client
spike. The compile-time elimination of the exact bug classes this very audit
found (dedup key swaps, `hot` bypass, naive-datetime crashes, falsy-Element
skips, missing-key "+?min", state-file races, sqlite leaks) is a real,
specific argument for Rust in the ingest/dedup core — those bugs are
*unrepresentable* under the newtype + enum + `Result` discipline. It is a
weak argument for the skills/LLM layer. A full big-bang rewrite is not
recommended; a Stage 0–3 port that leaves a clean seam at the skills boundary
captures most of the safety upside for a fraction of the risk.
