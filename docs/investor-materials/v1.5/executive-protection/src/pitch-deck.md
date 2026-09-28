## CTDI for Executive Protection

- Sovereign advance-work and movement-monitoring intelligence
- Self-hosted on ~$765 of owned edge hardware the operator controls and can unplug
- Built and run daily by a single founder — [operator LLC], LLC, Arlington, VA
- Investor briefing — executive-protection segment — August 2026
- Every claim re-verified live against the production system 2026-08-24; economics re-derived live 2026-09-03

## The problem

- Advance teams work from fragmented, non-integrated sources: flight status in one app, rail in another, weather and TFRs in browser tabs and phone calls
- The critical go/no-go call is made on gut feel; when a client, insurer, or lawyer asks "why did you roll?", there is no defensible record
- EP firms and GSOCs handle principal movement data they cannot push into third-party SaaS — sovereign options barely exist
- The data that matters most for protective advance work — FAA flow programs, arrival metering, terminal wind-shear, unfiltered blocked-aircraft visibility — is not for sale from any commercial vendor at any price

## The solution

- One sovereign platform ingesting aviation, rail, weather, and airspace — including all six FAA SWIM services under real, approved credentials
- A deterministic 6-factor go/no-go score anchored to 14 CFR 135.609 minimums — a 288-line auditable rule engine, not ML, not a black box; every state explainable to a client, insurer, or counsel
- Real-time alerts through self-hosted channels; protective-movement briefs written by a local LLM on the Pi — nothing leaves the hardware
- Runs entirely on hardware the client controls; principal itineraries, watchlists, and movement data never depend on a third-party cloud

## Capability for advance work

- Real-time flight tracking (6 FAA SWIM feeds + own-RF ADS-B); aircraft watchlists with hex-resolution, OOOI phase tracking, and real airline-reported 14-day on-time history with delay-drift flags
- Live Amtrak rail tracking; DC-area METARs; NWS/NWWS-OI severe-weather alerts; TFR and NAS airspace awareness
- Scheduled on-device `ep-advance`/`ep` protective-movement briefs — 21 local models, $0 cloud spend (measured over 35,217 calls, 57 days)
- Cross-category entity tracking across six intelligence watches + OSINT, with independent-feed corroboration, a human review gate, and silence/embargo detection
- Institutional memory: a 6,742-document knowledge vault under a semantic + causal layer (99 concepts, 51,317 edges), recompiled daily — advance knowledge that accumulates on client-controlled hardware

## Security is the differentiator — not a footnote

- EP buyers evaluate their own tooling the way they evaluate a venue: by finding what's wrong with it. This platform is built to reward that
- Controls re-tested live on a recurring cadence — bounded, non-destructive passes 2026-08-13 and 2026-08-24; stated plainly as founder-run self-assessment, not a third-party audit
- Bearer-token-only auth, network origin grants no tier; anonymous, forged, malformed, and spoofed-origin probes all rejected (403) live, including under adversarial header variants
- Credentials stored as one-way hashes only; secrets file owner-only; GPG-signed whole-tree manifest raises and refuses to execute on any tamper; every admin action audit-logged (actor/action/tier/IP/payload)

## We find our own issues — and close them same-day

- 08-24 testing found two real issues in our own system: the repaired public demo briefly had the production database mounted (writable), and shared the production chat file
- Both fixed the same day — demo relocated onto a hardened sibling directory containing no production file at all
- An adversarial re-verification then confirmed the fix live against the running container: demo can no longer see, read, or write any production file; nothing reopened; no new exposure found
- For this audience, a review that finds real things and closes them under pressure is a stronger signal than a suspiciously clean record — and the isolation is now structurally stronger than before

## Principal-location data protection — architectural, not promised

- Real receiver GPS coordinates live only in the owner-only secrets file, never in tracked files, and are on the public-mirror scrub list — verifiable in code
- The coordinate endpoint trust-gates the real location; an untrusted caller gets a fixed placeholder
- The public demo container structurally cannot leak coordinates — it loads only the non-secret config, so it has no secret to leak
- Open and disclosed: the trust boundary currently depends on a Cloudflare header with no app-layer backstop — named as a hardening candidate, not hidden (not exploitable in today's topology)

## The economics

- Runs on ~$765 one-time hardware + ~$22-38/yr electricity; $0 data-feed fees; $0 cloud-LLM spend — measured, not assumed
- Purchasable subset of this vertical's live capability lists at ~$59.3k-255.3k/yr — an EP/GSOC buyer needs complements summed (event detection + OSINT monitoring + critical-event notification + movement monitoring + push), where the platform-wide ~$55.2k-230.4k/yr band takes one correlation platform; independent figures, never summed
- No product in that basket does TFR/airspace-threat correlation or airline-reported OOOI movement monitoring at all — the floor buys adjacent capability, not equivalence
- Honest split: actual avoided cost is ~$2.2-2.7k/yr (mostly barter); the $59.3k-255.3k figure is replacement cost for a buyer without FAA vetting — never presented as avoided spend
- Ten live capabilities cannot be bought at any price: TFMS flow programs, TBFM metering, ITWS wind-shear, unfiltered blocked-aircraft visibility, receive-side ACARS/VDL, a permanently-owned corpus, and the signed execution gate

## Licensing — BSL 1.1

- Business Source License 1.1 adopted 2026-08-24 — Licensor: [operator LLC], LLC
- Free always for non-production use; free in production for personal self-hosting and internal relay/middleware use
- Commercial license required the moment CTDI touches any fee-based client service — even invisibly, even bundled into a retainer; hosted resale and white-labeling always require one
- Each version converts to GPL v3-or-later four years after publication (Change Date 2030-08-24) — protected now, credibly open later
- Disclosed: use-grant language under legal review, not yet counsel-confirmed

## Current state — honest tiers

- LIVE and VERIFIED: six SWIM feeds, CPS engine, watchlists, alerting, on-device briefs, entity tracking, knowledge layer, audit trail, integrity chain
- LIVE with disclosed caveats: feeds duty-cycled under load governance (10 shed/restore cycles in ~32 h — no SLA); ~42% LLM deterministic-fallback rate; ACARS RF received but DB fusion pending
- ROADMAP / NOT BUILT — stated plainly: multi-tenancy, billing, CRM, backup/DR, CI, CPS unit test, maritime AIS (fully dormant), demand (runsheet shows one trip)
- Withdrawn: MCP integration (retired 2026-08-18), always-on availability, and — pending a gating decision — a live public demo link

## Business status

- Pre-revenue by construction: no billing or customer-account code; the company site deliberately does not market CTDI yet
- Reference deployment: the founder's own executive-chauffeur operation, running continuously since June 2026
- Data-rights model: each client holds its own source credentials; CTDI ships software, not redistributed data — template-supported today
- Demo machinery is real: a 2.45 GB recorded-replay corpus with isolation adversarially verified; demo access arranged in diligence conversations

## Roadmap

- Productize: multi-tenancy, billing/entitlements, backup and DR, CI, CPS unit test
- Commission a first third-party security assessment; close the disclosed trust-boundary dependency and credential-lifecycle hygiene
- Formalize data-source agreements and finalize BSL terms with counsel
- Sign 2-3 EP / secure-transport / GSOC design partners via the replay-demo motion; co-define pricing
- First engineering hire to retire bus-factor-1
- Founder-authoritative mode targets: maritime AIS end 2026; eVTOL operational awareness spring 2027

## The ask

- EP, secure-transport, and GSOC design partners to pilot CTDI on hardware they control — plus seed capital to fund the roadmap
- No valuation, revenue projection, or market-size figure is presented — the fact base does not yet support one, and we will not invent numbers
- What you get today: a running, re-verified, integrity-gated sovereign platform; a permanently-owned data corpus; a defensible economic story; and a security culture that finds and fixes its own issues in the open
- Next step: live walkthrough of the production system and demo replay — info@example.com
