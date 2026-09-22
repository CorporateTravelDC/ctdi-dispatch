# Webhook Receiver Status — 2026-09-21

## What's live now

`src/web/routes/webhooks.py` — all three receivers real and wired in:
`POST /webhooks/limoanywhere/reservations`, `POST /webhooks/ringcentral/events`,
`POST /webhooks/3cx/events`.

**New this pass**: LimoAnywhere reservations now extract a flight or train
identifier from the payload and auto-add it to the real watchlist
(`POST /api/v1/watchlist/{flights,trains}` over loopback, admin-token
authenticated via `DISPATCH_ADMIN_TOKEN`). Flight identifiers get IATA→ICAO
normalization via the same carrier-prefix table `flight-hifi-track`'s skill
uses; train identifiers are passed through as bare numbers, matching the real
Amtrak feed schema (`nec-train-hifi-track`'s skill, verified live 2026-07-29).
RingCentral and 3CX remain event-log + notify only — call events don't carry
trip data the way a reservation payload does, so extraction doesn't apply
there.

**Field-name assumptions — NOT verified against a real vendor payload.**
LimoAnywhere's actual Customer API reservation schema was not available in
this environment. `_extract_flight_identifier`/`_extract_train_identifier`
check `flight_number`/`flight_no`/`flightNumber`/`arrival_flight`/
`departure_flight`/`flight` and `train_number`/`train_num`/`trainNumber`/
`train`, at the top level and nested under `trip`/`itinerary`/
`transportation`/`flight_info`/`reservation_detail`. These are reasonable,
common patterns for this class of API, not confirmed field names. If a real
delivery doesn't match, the existing log+notify behavior is the correct
fallback — not a bug, not silent data loss (the raw payload is always
persisted via `db.insert_webhook_event()` regardless of extraction success).
**First real action item once live traffic exists: capture one real payload
and correct these field names against it.**

## Blocker: real inbound delivery won't reach these endpoints today

Confirmed live: the only public hostname routing to the web service,
`dispatch.example.com`, is Cloudflare-Access-gated (Tailscale +
auth required) — its own `~/.cloudflared/config.yml` comment says so
explicitly. LimoAnywhere/RingCentral/3CX's own servers can't complete an
interactive Access login, so a real webhook delivery from any of them would
be blocked before ever reaching this code, regardless of how correct the
`X-Webhook-Secret` gate is.

**Options (operator decision, not made here):**
1. **Path-scoped Cloudflare Access bypass** for `/webhooks/*` on the existing
   `dispatch.example.com` hostname — narrowest change, keeps
   everything else on that hostname gated. This repo already has precedent
   for exactly this shape of fix: `docs/ALERT_REFERENCE.md`'s
   `approval-gate` topic entry describes a "narrowly-scoped
   `dispatch-approval-resolve-bypass` Access app" fixing the same class of
   problem for a different endpoint.
2. **A separate, ungated hostname** dedicated to inbound webhooks only (e.g.
   `webhooks.example.com` → same `127.0.0.1:80` nginx vhost,
   nginx path-routes only `/webhooks/*` through). Slightly more surface to
   maintain, but keeps the Access-gated hostname's threat model completely
   unchanged.
3. Leave it gated and have LimoAnywhere/RingCentral/3CX deliver to something
   else (a small relay) that forwards over Tailscale — most work, most
   moving parts, not recommended without a specific reason to prefer it.

Do not implement any of these without the operator's explicit choice — this
box has real, documented pentest history (see any `*-demo.container`
quadlet's comments) of exactly this class of mistake (a container assumed
gated that wasn't), so a new intentional public-surface decision deserves
the same care those fixes got, not a default.

## Secrets — not yet configured, needs the operator's own action

None of `LIMOANYWHERE_WEBHOOK_SECRET` / `RINGCENTRAL_WEBHOOK_SECRET` /
`THREECX_WEBHOOK_SECRET` exist in `dispatch-secrets.env` yet — every
endpoint currently 503s "not configured." Generating and writing these
wasn't attempted here (writing to the secrets store is a category Claude
Code's own auto-mode classifier has blocked for this session before, for
this exact class of action — not something to route around). Exact commands
to run, once real values (or generated random ones, if no vendor-issued
secret is needed since this is our side of a shared-secret scheme, not
theirs) are ready:

```bash
LIMOANYWHERE_WEBHOOK_SECRET=$(openssl rand -hex 32)
RINGCENTRAL_WEBHOOK_SECRET=$(openssl rand -hex 32)
THREECX_WEBHOOK_SECRET=$(openssl rand -hex 32)
{
  echo "LIMOANYWHERE_WEBHOOK_SECRET=$LIMOANYWHERE_WEBHOOK_SECRET"
  echo "RINGCENTRAL_WEBHOOK_SECRET=$RINGCENTRAL_WEBHOOK_SECRET"
  echo "THREECX_WEBHOOK_SECRET=$THREECX_WEBHOOK_SECRET"
} >> /etc/corporatetraveldc/dispatch-secrets.env
# then configure the SAME values as the outbound custom header on each
# platform's own webhook-delivery settings, and restart web to pick them up:
systemctl --user restart corporatetraveldc-web.service
```

## Test coverage

`tests/web/test_webhooks.py` extended with coverage for the new extraction +
auto-watchlist logic (flight found → watchlist call made with normalized
ICAO identifier; train found → watchlist call made with bare number; neither
found → no watchlist call, existing behavior unchanged; watchlist call
failure → webhook still returns 200, event still persisted). See test run
output in this pass's report.
