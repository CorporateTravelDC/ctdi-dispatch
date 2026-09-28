# Reservation/Call Webhook Integration Spec

**Version:** 1.0 **Date:** 2026-09-21
**Applies to:** inbound reservation and call-event webhooks → automatic
flight/train watchlist tracking.

---

## 1. Overview

CTDI can add a flight or train to its watchlist the moment a reservation is
created in your booking or livery software, with no manual entry. The
mechanism is three parts, each independently swappable:

1. **A networking/gateway layer** that gets an inbound HTTPS request from
   your reservation platform's servers to CTDI's webhook receiver.
2. **A webhook receiver** (this repo ships one, built on FastAPI) that
   authenticates the request, extracts a flight or train identifier from the
   payload, and calls the watchlist API.
3. **The watchlist API** (`POST /api/v1/watchlist/flights` /
   `POST /api/v1/watchlist/trains`) — already documented elsewhere in this
   repo; this spec treats it as the downstream contract.

**Explicit design goal**: none of the three layers require a specific
vendor. The reference deployment happens to use Cloudflare Tunnel +
Cloudflare Access for layer 1 and LimoAnywhere for the reservation source —
neither is load-bearing. Sections 3–4 give the Cloudflare example alongside
AWS, GCP, self-hosted, and VPN-only alternatives; section 6 gives the
LimoAnywhere field mapping alongside the pattern for swapping in a different
reservation platform.

---

## 2. Networking layer requirements (vendor-neutral)

Whatever you put in front of the webhook receiver must satisfy these
properties — the specific product doesn't matter, the properties do:

- **TLS termination** somewhere between the public internet and the
  receiver. Reservation platforms deliver over HTTPS; nothing here works
  over plain HTTP.
- **Reachability scoped to `/webhooks/*` only, not your whole deployment.**
  CTDI's admin surface, watchlist reads, and everything else should stay
  behind whatever access control you already run (VPN, IP allowlist, an
  identity-aware proxy). Only the three webhook paths need to accept
  unauthenticated-at-the-network-layer traffic from your vendor's servers —
  authentication happens at the application layer instead (next bullet).
  Keeping this scoped to an exact path, not a whole hostname, is the same
  principle CTDI already applies elsewhere in this repo for its other
  narrow public-facing exceptions (see `docs/SECURITY.md`).
- **Your gateway must not strip the credential you're using.** This is the
  part that's easy to get wrong and hard to notice until a real delivery
  fails silently. Some gateways (including Cloudflare Access in front of a
  Tunnel, in the reference deployment's own experience) strip inbound
  `Authorization` headers before they reach the origin. If your auth
  mechanism lives in a header, **verify live** that your specific gateway
  passes it through — don't assume. If it doesn't, use one of:
  - **A body-embedded HMAC-SHA256 signature** instead of a header: the
    sender computes `HMAC-SHA256(shared_secret, raw_request_body)`, includes
    it as a field in the JSON payload itself (headers get stripped, bodies
    generally don't), and the receiver recomputes and compares it with a
    constant-time comparison (`hmac.compare_digest`, matching the same
    timing-side-channel discipline this repo's header-based check already
    uses — see `secrets.compare_digest` in `src/web/routes/webhooks.py`).
  - **A path-embedded, single-use token** — this repo has a real, live
    precedent for exactly this shape elsewhere (a narrowly-scoped access
    exception for one specific URL pattern, using a random token as part of
    the path itself rather than a header, specifically because the
    reference deployment's own gateway strips headers on that route). The
    same idea generalizes: `POST /webhooks/limoanywhere/<random-token>/reservations`
    instead of a header, if your reservation platform's webhook config
    supports a custom URL but not a custom header.
- **Fail closed.** A missing or invalid credential must reject the request
  (401/403), never silently accept it. The reference implementation in this
  repo returns 503 if no secret is configured at all (so it's obviously
  inert, not silently open) and 401 on a wrong/missing secret — never a
  bare 200 for unauthenticated traffic.

---

## 3. Reference implementation — Cloudflare

The reference deployment runs Cloudflare Tunnel in front of the whole
dispatch stack, with Cloudflare Access gating everything on the tunnel's
hostname behind an identity check. That's the right posture for the admin
surface, but it also blocks a third-party webhook sender, which can't
complete an interactive login. The fix is a **narrowly-scoped Access
bypass application**, exact-path-matched to `/webhooks/*` only — everything
else on that hostname stays gated exactly as before. This is the same
shape of fix this deployment already uses for a different narrow exception
elsewhere in its admin surface (a single-use, URL-embedded token instead of
a header, for the same "Cloudflare strips Authorization in transit" reason
called out in section 2).

If you're running Cloudflare yourself: create a Cloudflare Access
application scoped to `your-hostname.example.com/webhooks/*` with a "bypass"
policy (no identity check on that path), leave every other path on the same
hostname under your normal Access policy, and use the body-embedded HMAC
scheme from section 2 for the application-layer credential, since Access
bypass alone doesn't add its own auth — it just stops blocking the request
before it reaches your app.

---

## 4. Alternative implementations

These are shapes, not copy-paste configs — validate against your own
provider's current documentation before deploying.

**AWS**: API Gateway (HTTP API) with a route scoped to `/webhooks/*`,
integrated to your origin (ECS/EC2/Lambda) via a VPC link if your origin is
private. CloudFront in front of API Gateway if you want WAF rules or rate
limiting at the edge. The HMAC-in-body pattern from section 2 works
unchanged; API Gateway doesn't strip headers by default, but a
WAF/CloudFront layer in front of it might, so verify.

**GCP**: Cloud Load Balancing with a URL map routing `/webhooks/*` to your
backend service, Cloud Armor for WAF/rate-limiting at the edge if wanted.
Same header-stripping caveat applies to any WAF layer.

**Self-hosted reverse proxy** (nginx, Caddy, Traefik): a `location /webhooks/`
block (nginx) or equivalent path match, proxying only that path to your
webhook receiver — nothing else on the same public IP/hostname needs to be
reachable. This is the lowest-dependency option if you don't want a cloud
gateway at all.

**VPN-only / no public exposure**: if your reservation platform supports
delivering webhooks over a site-to-site VPN or you'd rather not expose
anything publicly, skip the gateway layer entirely and have your vendor (or
a small relay you control on their side) push over the VPN tunnel directly
to the receiver's internal address. If your vendor only supports polling,
not push, a cron job hitting their reservation API every few minutes and
diffing against what's already tracked achieves the same result with
higher latency — see the existing "Platform-specific notes" table above in
this repo's main README for vendors that fall into this category.

---

## 5. Webhook payload contract

This is the receiver as it exists in code today
(`src/web/routes/webhooks.py`), documented precisely — not aspirational.

### `POST /webhooks/limoanywhere/reservations`

**Headers**: `X-Webhook-Secret: <shared secret>` (required; 503 if the
receiver has no secret configured at all, 401 if the header is missing or
wrong).

**Body** (JSON): a reservation payload. The receiver reads
`reservation_event` (event type string, e.g. `reservation.created`) and
`id`/`reservation_id` (external reference) from the top level, and attempts
flight/train extraction per section 6 below. No other fields are required —
an unrecognized payload shape still gets logged and stored, just without
auto-tracking.

**Response**: `200` with
```json
{"status": "accepted", "event": "<event type>", "auto_tracked": "flight AAL2773"}
```
`auto_tracked` is only present when a flight or train identifier was
successfully extracted and added to the watchlist; its absence is normal,
not an error.

### `POST /webhooks/ringcentral/events`

**Headers**: `X-Webhook-Secret` as above, OR `Validation-Token` on
RingCentral's one-time subscription-verification handshake (echoed back
verbatim, no secret required for that specific handshake request — it
carries no event payload and authorizes nothing on its own).

**Body**: a RingCentral call/SMS/voicemail event. `event` and `uuid` are
read for logging; no extraction happens (call events don't carry trip
data).

**Response**: `200` with `{"status": "accepted", "event": "<event type>"}`.

### `POST /webhooks/3cx/events`

**Headers**: `X-Webhook-Secret` as above.

**Body**: a 3CX call control event. `event_type`/`Event` and
`call_id`/`CallId` are read for logging; no extraction, same reasoning as
RingCentral.

**Response**: same shape as RingCentral's.

All three persist the full raw payload (event log, for audit/replay) before
attempting anything else, so a failed or unrecognized extraction never
means lost data — only lost automation for that one event.

---

## 6. Reservation-platform field mapping

The **extraction contract** — what comes out the other side — is the
vendor-neutral part: a flight identifier normalized to its real ICAO
callsign form (e.g. `AAL2773`, never the bare flight number `2773` or the
IATA form `AA2773`), or a train identifier as a bare number (e.g. `79`,
matching Amtrak's own numbering — no carrier-prefix normalization applies
to trains).

The **field names you read those values from** are the vendor-specific
adapter — this is the part you customize per reservation platform.

The reference implementation's current field-name assumptions (for
LimoAnywhere's Customer API), checked at the payload's top level and then
under a `trip`/`itinerary`/`transportation`/`flight_info`/
`reservation_detail` nested object if present:

| Looking for | Field names checked |
|---|---|
| Flight number | `flight_number`, `flight_no`, `flightNumber`, `arrival_flight`, `departure_flight`, `flight` |
| Train number | `train_number`, `train_num`, `trainNumber`, `train` |

**These are reasonable, common patterns for this class of API — not
confirmed against LimoAnywhere's actual schema** (no vendor sandbox access
at implementation time). If you're integrating a different platform (Livery
Coach, GroundWidgets, an in-house system), the adapter work is exactly this:
find your platform's equivalent field(s), map them into the same extraction
function, keep the normalization step (IATA→ICAO for flights, bare-number
pass-through for trains) unchanged.

---

## 7. Downstream watchlist API contract

**Note on this repo's own docs**: the main `README.md`'s "Reservation
System Integration" section documents a single bundled
`POST /api/v1/watchlist` endpoint with a `{"type": "flight"|"train", ...}`
body. The endpoints actually implemented today are **two separate routes**,
documented below — treat this section, not that one, as current; the
README predates the split. (Flagged here rather than silently reconciled —
worth a follow-up pass to align them.)

### `POST /api/v1/watchlist/flights`

**Auth**: Admin-tier bearer token (see `docs/auth-token-proxy-pattern.md`
for the token-tier model).

**Body**:
```json
{
  "identifier": "AAL2773",
  "origin": "ORD",
  "destination": "IAD",
  "scheduled_departure": "2026-09-21T18:00:00Z",
  "scheduled_arrival": "2026-09-21T20:30:00Z",
  "auto_remove_at": "2026-09-22T02:30:00Z",
  "notes": "Auto-added from reservation #12345",
  "added_by": "your-webhook-source-name",
  "hex_id": null,
  "registration": null
}
```
Only `identifier` is required. `identifier` must be the real ICAO callsign
(section 6) — the platform's own flight-plan cross-check (FAA FDPS) uses it
as the lookup key and will silently fail to resolve on an IATA-form or bare
flight number. `hex_id`/`registration` are optional; omit them at
reservation-add-time (the platform resolves and locks the aircraft identity
once the flight is actually airborne and confirmed) rather than guessing.
`auto_remove_at` defaults to 6 hours past scheduled arrival if omitted, or
24 hours from add-time if no arrival estimate exists yet.

**Response**: `201` with the created watchlist entry, including
`fdps_confirmed` (whether FAA's own live flight-plan feed currently shows
this flight as filed) and `ontime_history_14d` (14-day on-time performance
for this route, when available).

### `POST /api/v1/watchlist/trains`

Same shape, minus the aviation-specific fields (`hex_id`, `registration`,
`fdps_confirmed`). `identifier` is the bare train number. `auto_remove_at`
defaults to 3 hours past scheduled arrival.

---

## 8. Security posture

- **Fail closed everywhere**: an unconfigured secret returns 503 (visibly
  inert), never a silent accept; a wrong/missing secret returns 401; a
  watchlist-add failure during extraction never turns an already-persisted,
  successfully-received webhook into an error response — the event is
  logged regardless of what automation does with it afterward.
- **Narrow scope**: whatever gateway mechanism you use, scope it to
  `/webhooks/*` exactly, not your whole deployment's public surface.
- **No shared secrets belong in a public repo** — obviously, but worth
  saying plainly: the values that go in `LIMOANYWHERE_WEBHOOK_SECRET` etc.
  are yours to generate and keep in your own secrets store, never committed
  anywhere.
- **Forward-compatibility note, not a design**: this platform may in the
  future add a network-federation authentication layer for coordinating
  across multiple sites/deployments. Nothing here depends on that or
  implements it — it doesn't exist yet. But if you're setting up webhook
  credentials today and want an easy migration path later, prefer
  narrowly-scoped, rotatable credentials (one secret per source, easy to
  regenerate independently) over a single long-lived broad one. That
  property costs nothing now and makes a future migration a credential
  swap instead of a redesign.

---

*See also:* [docs/auth-token-proxy-pattern.md](auth-token-proxy-pattern.md)
— the token-tier model referenced in section 7.
*See also:* the main `README.md`'s "Reservation System Integration"
section — being reconciled with this spec, see the note in section 7.
