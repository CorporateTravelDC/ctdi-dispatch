# Security review 01 — agent trust-model documentation pass (2026-10-07)

> **Review record. Frozen.** Part of the security review chain (`README.md` in this directory). The canonical, current trust model is `docs/AGENT_TRUST_MODEL.md`. The document this pass produced is preserved unchanged in `2026-10-07-01-agent-trust-model.snapshot.md`. Next pass: `2026-10-07-02-hardening.md`.

## Why this pass ran

An adversarial external review of the sanitized public repository misclassified parts of the agent-signing and operator-approval design. The public repository showed how grants and signatures were *consumed* but did not make the full chain obvious: human, then approval, then delegated authority. The brief was to document the **existing, provable** properties for a hostile auditor, and to strengthen no claim beyond what the code enforces.

## Method

- **Code and configuration read in full:**
  - `src/common/governance.py`, `src/common/db.py` (approvals, audit), `src/auth/auth.py`, `src/web/main.py` (approval routes, `_board_auth`), `src/web/routes/console.py`;
  - `src/common/agent_gateway.py` and its routes, `src/common/es_invites.py`, `src/common/board_sign.py`;
  - `scripts/approve.sh`, `scripts/sudo-approval-gate.sh`, `scripts/sign-manifest.sh`, `scripts/grant-agent-session.sh`, `scripts/board-signer-ctl.sh`;
  - the reverse-proxy and tunnel configuration, and migrations 0062 and 0066–0073.
- **Live, read-only inspection:**
  - `sudo -n -l` and the firewall zones;
  - listening sockets;
  - the live connector-to-account mapping;
  - which key signed the manifest, and whether the approval key has a passphrase;
  - safe GETs against the console from loopback and the LAN address.
- **Adversarial checks:** existing tests, plus four new ones for:
  - approval after expiry;
  - double resolution;
  - one connector's token used on another;
  - a link signed after kill-all.
- **No destructive production testing.**

## Findings (as of this pass)

| # | Finding | Status found | Disposition |
|---|---|---|---|
| 1 | The Unix account is the root of trust: human authority is cryptographic against agent accounts and cloud connectors, policy-only against processes running as the operator account | structural | documented (§1); reframed as a designed exception in review 02 |
| 2 | Passwordless sudo for `dnf remove *`, `dnf autoremove`, `semanage port -a *` and dead `ollama*` units made the sudo approval gate advisory for its own patterns | NOT ENFORCED | fixed in review 02 |
| 3 | `POST /admin/approval-requests` accepted any TTL | PARTIAL | fixed in review 02 |
| 4 | Requester was a code-path string (`"admin-token"`); reason and pattern were not signed | PARTIAL / NOT ENFORCED | fixed in review 02 |
| 5 | The console redemption window after approval was bounded only by the browser cookie | PARTIAL | fixed in review 02 |
| 6 | Governance events (approvals, console, gateway, reader plane) were outside the hash-chained audit log, and nothing verified the chain | NOT ENFORCED | fixed in review 02 (events chained; verifier in the integrity sweep) |
| 7 | The agent manifest key has no passphrase and sits in the operator keyring; the grant check lives in the client script | POLICY ONLY | documented; designed exception (review 02, §0) |
| 8 | Re-enabling a connector and resuming reader sign-ins need a console session, not a signature | by design | documented; reopening the **gateway** needs a signature |
| 9 | On "normal" board routes the shared board key alone is accepted, so attribution there is claimed, not proven | by design | documented |

**Properties found stronger than earlier documentation** (now documented):

- browser-bound console sign-in, with one session per approval;
- the deny-only tap path;
- per-connector token binding;
- kill-all revokes **and** disables, and reopening needs a signature;
- the approval key is refused if it equals any board key;
- signature namespaces are separate.

## Validation

- Full suite: 943 passed at the end of this pass.
- New tests: `test_expired_request_cannot_be_signed_and_reads_as_expired`, `test_signed_resolution_is_single_use`, `test_one_connectors_token_is_refused_on_another_connector`, `test_a_link_signed_after_kill_all_never_becomes_a_token`, `tests/scripts/test_signing_identities_distinct.py`.
- Public leak gate: passed. The document was published to the public mirror.
