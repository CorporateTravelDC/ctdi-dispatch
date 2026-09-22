# Addenda

Optional capability extensions to the corporatetraveldc dispatch platform.
Each addendum is self-contained: its own directory, its own README, and
(where applicable) an `apply.py` script for applying to an existing checkout.

Addenda that ship in the main codebase are already applied -- their `apply.py`
scripts remain present for reference and will skip all steps cleanly if run.

---

## Available addenda

| Directory                      | Summary                                              | Status in main |
|-------------------------------|------------------------------------------------------|----------------|
| `wpc_forecast_discussions/`   | WPC national forecast discussions via NWWS-OI        | Code applied, **no data flowing** — see its README |

⚠️ "Applied" here means the code changes are merged into the main tree, not
that the capability is producing data. As of 2026-08-23 the WPC addendum's
`wpc_discussions` table has **zero rows ever** and
`GET /api/v1/wx/discussion` returns `{"available": false}`, despite the
`ttaaii`-key fix being deployed. Read
`addenda/wpc_forecast_discussions/README.md`'s status banner before citing
this as a working data source.

---

## Applying an addendum

```bash
python3 addenda/<name>/apply.py
```

Run from the repo root. Scripts are idempotent and create `.bak` files before
modifying anything.
