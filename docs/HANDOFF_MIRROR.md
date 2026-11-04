# Handoff mirror (bilateral /srv <-> vault)

Operator decision 2026-11-03. Cloud-gateway accounts (Cowork, ChatGPT) have no Unix session and
cannot read `/srv`; on-box accounts reach the vault only through the research route. The handoff
mirror keeps the two sides in step so neither needs the other's access.

| direction | source | sink | pointer |
|---|---|---|---|
| OUT, box to vault | text drafts (`.md`, `.txt`, up to 256 KiB) in `/srv/ctdc-handoff/<name>-from-<account>/` and `/srv/ctdc-handoff/research-intake/` (not `from-vault/`) | `01-Sources/personal-notes/Series/handoff/from-box/<YYYY-MM-DD>/<drop>/<file>.md`, create-only | `.../from-box/CURRENT.md`, rewritten when a run mirrors something; a dated `INDEX-<HHMMSS>.md` per run |
| IN, vault to box | contributions by `HANDOFF_MIRROR_CLOUD_ACCOUNTS` (default `ctdc-agent-anthropic-cowork`) under `01-Sources/personal-notes/Series/contributions/<task>/<account>/` | `/srv/ctdc-handoff/research-intake/from-vault/<account>/<task>/<file>.md` | `research-intake/from-vault/CURRENT.md` |

`research-intake/` is therefore the one bilateral folder.

Rules the code holds (`tests/scripts/test_handoff_mirror.py`):
- Every OUT body passes the CUI/PII scrub gate (`second_brain.scrub_gate`); a blocked file is listed
  as withheld, never partially mirrored. Registration-shaped tokens are replaced by
  `[tail withheld]` (LADD status is not checked by the mirror).
- Patches, archives and binaries stay on the box; the index lists them by sha256.
- Idempotent by a content-hash state file (`~/.local/state/corporatetraveldc/handoff_mirror_state.json`);
  never deletes on either side. The IN side never feeds back into OUT.
- Start line `HANDOFF_MIRROR_SINCE` (default 2026-11-03T00:00:00Z): the earlier Codex backlog was
  mirrored once by hand (vault `Series/contributions/handoff-from-codex-20261103/`).
- Load-aware: a run skips itself at load1 >= `HANDOFF_MIRROR_LOAD_MAX` (24, the relay gate).

Units: `.config/systemd/user/corporatetraveldc-handoff-mirror.{service,timer}`, every 15 min at
`:08/:23/:38/:53` (+ up to 2 min), host-side under `scripts/with-dispatch-env.sh` so the shared
`/srv` folder is never relabelled for a container. `OnFailure` notifies.

Standing rule text for `01-Sources/manual/` (operator to adopt): see
`docs/HANDOFF_MIRROR_STANDING_RULE.md`.
