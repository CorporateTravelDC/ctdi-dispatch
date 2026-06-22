# Addendum: WPC National Forecast Discussions

## What this addendum adds

This addendum integrates Weather Prediction Center (WPC) national forecast
discussion products into the corporatetraveldc dispatch platform via the
existing NWWS-OI push feed. No additional credentials, subscriptions, or
infrastructure are required beyond an active NWWS-OI XMPP connection.

### Products ingested

| AWIPS ID | Product Name                        | Issuance cadence |
|----------|-------------------------------------|------------------|
| FXUS02   | Short Range Forecast Discussion     | ~4x/day, 12-48hr |
| FXUS06   | Medium Range Forecast Discussion    | 2x/day, D3-D7    |
| FXUS07   | Extended Forecast Discussion        | 2x/day, D6-D10   |
| FXUS05   | Short Range QPF Discussion          | 4x/day           |

All four products originate from `KWNO` (Weather Prediction Center,
College Park MD). They arrive on the same NWWS-OI MUC as local WFO products
and are handled by a dedicated branch in the ingest parser that runs before
the local WFO filter -- no changes to `NWWS_WFO_FILTER` are required.

### Why this matters for executive transport operations

Local WFO products (LWX, AKQ, CTP) answer the question: *what is happening
right now at specific locations?* WPC discussion products answer a different
question: *where are synoptic systems going, and when?*

For advance trip planning -- particularly 12-48 hours out -- the Short Range
Forecast Discussion (FXUS02) provides the forecaster's narrative reasoning
about system movement, model uncertainty, and confidence levels that no
point-observation product can supply. Combined with the existing CPS scoring
and METAR snapshot, this gives the dispatch platform a complete picture from
current conditions through the 48-hour planning horizon.

### What changes in the codebase

Three files are modified. All changes are additive:

**`src/common/db.py`**
- `SCHEMA_V12`: new `wpc_discussions` table with index on `(awips_id, issued_at DESC)`
- `init_db_v12()`: called at web container startup alongside v1-v11
- `upsert_wpc_discussion()`, `get_latest_wpc_discussion()`,
  `get_latest_wpc_discussions()`, `prune_wpc_discussions()` helpers

**`src/ingest/nwws.py`**
- `_WPC_PRODUCTS` dict: maps AWIPS IDs to human labels
- `_parse_wpc_issuance()`: parses the standard WPC header time string to
  unix epoch, with UTC offset handling for EDT/EST/CDT/CST/MST/MDT/PST/PDT
- `parse_wpc_product()`: returns `upsert_wpc_discussion` kwargs or None
- `_on_msg()`: updated with a `wfo == "KWNO"` branch before the local WFO
  filter; KWNO products are stored via the WPC path and return immediately

**`src/web/main.py`**
- `db.init_db_v12()` added to `startup()`
- `GET /api/v1/wx/discussion` (Tier 0): returns latest of all stored
  products, or a single product via `?product=FXUS02`
- `GET /api/v1/wx/discussion/{awips_id}` (Tier 0): path-form convenience

### New REST endpoints

```
GET /api/v1/wx/discussion
GET /api/v1/wx/discussion?product=FXUS02
GET /api/v1/wx/discussion/FXUS02
```

All Tier 0. Response includes `body` (full text, up to 8000 chars) and
`body_excerpt` (first 300 chars) for use in collapsed PWA cards.

---

## Applying to an existing deployment

If this addendum is not yet built into the base codebase, `apply.py` will
apply all changes cleanly to an existing checkout:

```bash
# From the repo root:
python3 addenda/wpc_forecast_discussions/apply.py
```

The script:
- Creates `.bak` files alongside each modified file before touching them
- Checks idempotency -- already-applied steps are skipped, not re-applied
- Rolls back all changes on any failure
- Prints `[OK]` / `[SKIP]` / `[FAIL]` for every step

After applying, rebuild and restart:

```bash
bash build-images.sh
systemctl --user restart corporatetraveldc-web.service
systemctl --user restart corporatetraveldc-ingest.service
```

The `wpc_discussions` table is created automatically on first web container
startup via `init_db_v12()`. No manual DB migration is required.

---

## Notes

- WPC discussions can run 3-4KB of text. Storage is capped at 8000 chars per
  row in `upsert_wpc_discussion()`. This covers all four product types with
  comfortable margin.
- `prune_wpc_discussions()` retains the 10 most recent rows per product by
  default. Call it from any scheduled cleanup task to bound table growth.
- The `issued_at` field is parsed from the product's own header timestamp,
  not from ingest time, so discussions retain their correct temporal position
  even if the ingest container reconnects after a gap.
- This addendum has no dependency on FAA SWIM credentials, NOTAM API keys,
  or any other credentialed feed. It requires only an active NWWS-OI session.
