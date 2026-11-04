# Standing rule: /srv <-> vault handoff mirror (draft for 01-Sources/manual/, 2026-11-03)

Proposed by Cowork on the board (brd-e7062ddeb9ca); the operator directed it on 2026-11-03, with
`research-intake` as the bilateral folder. The operator adopts it by filing it in the vault manual.

1. Any content a cloud-gateway agent (Cowork, ChatGPT) must consume that originates in a `/srv`
   handoff path (`/srv/ctdc-handoff/`, `/srv/ctdc-handoff/research-intake/`) is mirrored by the
   on-box handoff mirror into the vault under `01-Sources/personal-notes/Series/handoff/from-box/`,
   scrub-gated, create-only and attributed to its origin account, with a `CURRENT.md` pointer naming
   the newest dated folder, and each file's source path and sha256.
2. Cloud agents' contributions come back the other way into
   `/srv/ctdc-handoff/research-intake/from-vault/<account>/<task>/`, with its own `CURRENT.md`.
   `research-intake/` is the one bilateral folder.
3. Cloud agents never assume `/srv` is reachable. They read the mirror, and ask on the board if
   something they need is not there; an on-box account then mirrors it.
4. Code patches, archives and binaries are not mirrored; they stay on the box and are listed by
   sha256. Nothing CUI-adjacent is mirrored; a scrub-blocked file is listed as withheld.
5. Durable findings still go through `workspace-contribute.sh` into the vault; `/srv` stays hand-off
   and intake, not storage.
