---
name: "second-brain-remember"
description: "Capture a quick fact, quote, or note into the corporatetraveldc second-brain vault. Trigger on \"remember this\", \"add this to the second brain\", \"note for later\", \"save this for the vault\", or similar manual-capture requests."
---

---
name: second-brain-remember
description: Capture a quick fact, quote, or note into the corporatetraveldc second-brain vault. Trigger on "remember this", "add this to the second brain", "note for later", "save this for the vault", or similar manual-capture requests.
---

# second-brain-remember

Writes a manual note into the corporatetraveldc second-brain vault (Nextcloud, `corporatetraveldc/01-Sources/manual/`), through the same CUI/PII scrub gate and index pipeline as the automated ingest paths (daily digest, weekly compile, RSS poller).

## Trigger

Any request to capture a standalone fact, quote, decision, or observation for later reference — "remember this", "add to the second brain", "note that...", "save this for the vault". Not for structured operational data (flights, TFRs, briefs) — those have their own automated ingest paths. This is for the ad hoc stuff that would otherwise only live in chat.

## How to call it

```
POST http://100.x.x.x:8000/api/v1/remember
Authorization: Bearer $CTDC_ADMIN_TOKEN
Content-Type: application/json

{
  "text": "the fact/quote/note to remember",
  "tags": "comma,separated,tags"
}
```

`tags` is optional — defaults to `manual,high-priority` server-side if omitted. Always use the Tailscale base URL (`http://100.x.x.x:8000`); the Cloudflare Tunnel strips auth tokens on token-gated routes.

## Response

- `201` — `{"status": "ok", "path": "corporatetraveldc/01-Sources/manual/<timestamp>.md"}`. Report the path back to the operator briefly (or just confirm it's saved — the path itself isn't usually interesting to relay).
- `400` — empty/missing text. Ask the operator what to remember.
- `422` — blocked by the CUI/PII scrub gate. Do not retry with the same text. Tell the operator plainly what got blocked (the gate raises rather than redacts, on purpose — see `second_brain/scrub_gate.py`'s docstring for why) and let them decide whether to rephrase or drop it. Never attempt to work around the gate.
- `401`/`403` — token issue. Do not silently retry; flag it.

## Design notes

- Same code path as the CLI (`python3 -m second_brain.remember`, still available directly on the Pi for relay-mode use if the REST API is unreachable) — both call `second_brain.remember.remember_text()`, so nothing about the write/index/scrub-gate behavior differs between the two entry points.
- Every capture is indexed immediately (SQLite FTS) — searchable right away via `second_brain.index_db --search "..."` if you need to confirm something landed.
- This is deliberately for short, standalone captures. If the operator hands you something long-form or highly structured (a full document, a multi-section report), it still works mechanically but consider whether it actually belongs in one of the PARA folders (`02-Concepts`, `03-Entities`, `04-Syntheses`) instead — this endpoint always writes to `01-Sources/manual/`, no folder routing.

---

## Token resolution (added 2026-09-22) — `$CTDC_ADMIN_TOKEN`

This skill no longer carries a plaintext token. The old inline
`ctdc_cowork_…` literal was **revoked 2026-08-16** (auth_tokens id 3, cowork
downgraded admin -> shares), so every admin call here had been 403-ing for
five weeks before anyone noticed.

Resolve `$CTDC_ADMIN_TOKEN` at call time:

- **Shell on the Pi:** read `~/.secrets/remote-admin-agentic.token` — active
  admin token, auth_tokens id 23, prefix `ctdc_remote-admin_`, device
  `cowork`, expires 2027-09-20. Read it inside the command, never echo it:
  ```bash
  TOK="$(grep -oE 'ctdc_[A-Za-z0-9._-]+' ~/.secrets/remote-admin-agentic.token | head -1)"
  ```
- **MCP / no shell:** the client must supply it as `DISPATCH_TOKEN`.

Never construct a call whose error output could echo the Authorization
header. A 403 here means check `auth_tokens.revoked_at` first — a revoked
token is indistinguishable from an unprovisioned one at the client.
