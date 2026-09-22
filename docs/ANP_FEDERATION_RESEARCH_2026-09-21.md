# ANP Federation — Research and Gap Analysis

**Date:** 2026-09-21
**Status:** research only. Nothing built, nothing decided, no code changed.
**Purpose:** decision support for the operator's stated future direction —
making this platform ANP-aware for main-campus/satellite-campus or
main-affiliate/downstream-affiliate federation, as recorded in
`docs/MODEL_EVALUATION_2026-09-21.md`'s closing section.

**Bottom line up front:** ANP is real, actively developed, and has a
published spec and working Python SDK — but it is explicitly not
production-ready as of today, its identity layer is at MVP stage, and it
has essentially no production adoption. Independent analysis puts viable
production use at 2027 or later. Meanwhile, roughly 80% of the work
federation actually requires of *this* platform is protocol-independent
and could be built now against the platform's own existing primitives.
**The honest recommendation is to decouple the two:** treat "federation
readiness" as the near-term project and "ANP as the wire protocol" as a
deferred, swappable decision.

---

## 1. What ANP actually is, today

### The short version

Agent Network Protocol (ANP) is an open-source protocol suite for
agent-to-agent communication over the open internet. Its stated vision is
to be "the HTTP of the Agentic Web" — decentralized, peer-to-peer agent
identity, discovery, negotiation, and messaging, with no central broker.

### Verified facts

| Item | Finding | Source |
|---|---|---|
| License | Apache-2.0 | GitHub API, checked directly |
| Spec repo | `agent-network-protocol/AgentNetworkProtocol` — 1,433 stars, 103 forks, 24 open issues | GitHub API |
| Last spec activity | 2026-09-20 (yesterday) — active | GitHub API |
| Current release | **v1.1**, published 2026-06-27 (v1.0 was 2025-05-19) | GitHub API |
| Reference SDK | `agent-network-protocol/anp` — Python, 350 stars, last push 2026-09-21 | GitHub API |
| PyPI package | `anp` v1.0.3, `requires_python >=3.8` — installable | PyPI API |
| Standards venue | W3C **AI Agent Protocol Community Group**, proposed 2025-05-08, first meeting 2025-06-18 | W3C |

A Community Group is the *lowest-commitment* venue at W3C. It is not a
Working Group and produces no Recommendation-track standard. CG reports
carry no standing as a web standard. This matters for a compliance
argument: "conforms to a W3C Community Group report" is meaningfully
weaker than "conforms to a W3C Recommendation," and an auditor may or may
not accept the distinction as material.

### Architecture, as specified

- **Identity:** W3C Decentralized Identifiers (DIDs), specifically the
  `did:wba` method — each identifier resolves to an HTTPS-hosted DID
  document. `did:wba` is itself a **draft** DID method specification.
- **Description:** Agent Description Protocol (ADP), JSON-LD documents
  carrying name, capabilities, supported protocols, auth schemes, endpoints.
- **Discovery:** a well-known endpoint, `.well-known/agent-descriptions`.
- **Interfaces:** both structured (JSON-RPC, OpenAPI) and natural-language
  (YAML-described).
- **v1.1 suite scope:** `did:wba` identity, WNS handles, agent description,
  agent discovery, end-to-end instant messaging, and AP2 agent payment.

### Maturity — the honest read

This is the part worth being blunt about, because it is an architecture bet.

Independent 2026 analysis is direct: ANP is *"a long-term infrastructure
bet, not a deploy-today solution. Organizations building for a 2-3 year
horizon should track it; those building for production in 2026 should not
depend on it."* The same analysis notes *"DID resolver infrastructure is
still maturing, tooling and library support lag significantly behind
HTTP-based protocols, and the meta-protocol negotiation overhead adds
latency to every connection,"* and estimates production viability at a
*"2027 timeframe"* or later.

The approach has been characterized elsewhere as *"technically compelling
but not yet ecosystem-ready."*

Three corroborating signals from direct repo inspection, not commentary:

1. **Recent spec commits are all `docs:`** — "extract DID authentication
   and generalize messaging methods," "align shared agent and verification
   guidance." That is specification refinement, not implementation hardening.
2. **A commit branch named `feat/did-web-mvp-20260914`** merged into the
   SDK release line in September 2026. The identity layer — the entire
   foundation of ANP's trust model — is at **MVP** stage as of this month.
3. **Ecosystem repos are stale.** `anp-examples` (18★), `anp-open-sdk`
   (13★), `did-wba-example` (6★) were all last pushed mid-2025. The two
   live repos are the spec and the first-party SDK. There is no visible
   third-party implementation ecosystem.

**Adoption:** I could find no named production deployment, enterprise
pilot, or commercial implementation of ANP. By contrast MCP, A2A, and ACP
all have named production users. I want to be precise about the epistemic
status here: absence of *published* adoption is not proof of absence of
adoption, but for a protocol whose whole value proposition is network
effects between independent parties, an invisible network is close to the
same thing.

### Disambiguation

The acronym "ANP" is overloaded. The operator's phrasing was "Agentic
Network Protocol"; the actual project is **Agent Network Protocol**
(agent-network-protocol.com). I treated these as the same intent. ANP is
also, unrelatedly, a common abbreviation in medicine (atrial natriuretic
peptide) and nursing credentials — irrelevant here but worth noting if
anyone searches loosely.

### Relationship to MCP and A2A

These are not competitors occupying one slot; they sit at different layers.

- **MCP** — connects one agent/model to *tools and context*. Client-to-server.
  This is what the platform already used.
- **A2A** — connects agents to *each other* within a mostly-trusted
  environment, typically with existing enterprise identity underneath.
- **ANP** — connects agents across *organizational and trust boundaries*
  on the open internet, with decentralized identity as the trust root.

The convergence read from 2026 analysis: MCP, A2A, and ACP are layering
together into a stack; **ANP is tracking as a parallel, later track** —
not expected to win, not expected to be absorbed, but occupying a separate
strategic niche for decentralized cross-internet collaboration.

That is actually *consistent* with the operator's framing — "in addition
to MCP, not replacing it" is the correct mental model, not a hedge.

There is also a first-party **`mcp2anp`** bridge (7★, last push 2026-01-13)
that converts MCP to ANP. Low activity, but its existence confirms the
two-protocol coexistence path is anticipated by the ANP project itself.

### What I could not confirm

- **ANP-specific security findings.** There is a directly relevant paper —
  *"Security Threat Modeling for Emerging AI-Agent Protocols: A Comparative
  Analysis of MCP, A2A, Agora, and ANP"* (arXiv 2602.11327). I could not
  extract its ANP-specific conclusions; the PDF text layer would not parse
  and the abstract page carries only a summary ("twelve protocol-level
  risks," with a case study on MCP, not ANP). **This is a real gap in this
  document.** Before committing to ANP, someone should read that paper
  properly — it is the single most on-point source for whether ANP's trust
  model survives contact with a regulated-industry threat model.
- Whether any regulated-industry deployment has accepted a DID-based
  identity chain for audit purposes. I found general literature on the
  tension (below) but no precedent.

### The regulated-industry tension with DIDs — flagging early

This is the part most likely to bite, given the operator's hard constraint.

General decentralized-identity literature identifies a structural problem:
DID systems are *designed* for privacy and pseudonymity, and regulators
*"encounter difficulties in tracking and auditing transactions"* in the
absence of explicit identity information. Separately, **revocation** in
decentralized systems is noted as *"particularly difficult due to caching
delays, asynchronous state updates, and network latency, which can
temporarily allow invalid credentials to be accepted."*

Read that second point against the operator's own requirement — *revocation
of a downstream affiliate* — and it is a direct collision. A local
`revoked_at` column flip (what this platform does today) is instantaneous
and total. A DID-based revocation is eventually-consistent and may have a
window where a revoked affiliate still transacts. **That window needs an
explicit answer before ANP is load-bearing for authorization**, not after.

---

## 2. What federation would require of *this* platform

Grounded in the real code, not the abstract.

### What exists today

**Identity and auth** (`src/auth/auth.py`, `src/ctdc_token/cli.py`,
`auth_tokens` table in `pg_schema/0002_schema.sql`):
- Four tiers: `T0` (unauth), `T1` (CERT/Tailscale-origin), `T2`, `ADMIN`.
- Tokens: `ctdc_<user>_<32-char-random>`, SHA-256 hashed at rest, plaintext
  shown exactly once. Columns: `token_hash`, `token_prefix`, `user_label`,
  `tier`, `device_label`, `created_at`, `expires_at`, `revoked_at`.
- **Entirely local.** One Postgres table, one site. There is no concept of
  a token issued by a *foreign* authority, no issuer field, no trust root
  beyond "it is in our table."

**Audit** (`audit_log` table, `db.audit()`, `require_admin` dependency):
- 32 admin endpoints write audit rows through the shared `require_admin`
  factory. Plus SR-1/SR-2 skill-runtime events, Tier-2 CUI reads, and board
  token rotation.
- Schema: `event_time`, `action`, `tier`, `token_prefix` (first 8 chars
  only, never the full token), `remote_addr`, `detail` (JSON).
- 90-day retention via `db.prune_audit_log()` + a daily poller skill.

**Integrity** (`scripts/verify-manifest.sh`, `sign-manifest.sh`,
`scrub-public-tree.py`):
- GPG-signed whole-repo-tree manifest, 1,032 files covered. Containers
  fail-closed at entrypoint if the tree does not match the signed manifest.
  Separate agent signing key from the operator's own, so a signature's key
  ID is itself an audit trail of who produced it.

**Egress control** (`src/second_brain/scrub_gate.py`,
`scripts/scrub-public-tree.py`):
- A fail-closed scrub gate that *raises* rather than silently redacting.
- Allowlist-based post-scan of output, plus a hard-fail check that reads
  live secret values out of `dispatch-secrets.env` and refuses to ship if
  any appear anywhere in output. Built after a real 2026-08-24 leak incident.

**Transport:** Tailscale tailnet for internal/T1 reach; Cloudflare Tunnel
for narrow public surfaces, with narrowly-scoped CF Access bypass apps for
specific paths.

**MCP:** retired from deployment 2026-08-18 (checkout archived), repo
still exists standalone. Architecturally it was a **thin HTTP client over
the dispatch REST API** — a client-side adapter, not a server-side
capability surface.

### The one genuinely reusable federation primitive

The **board token system** (`src/common/db.py`, `board_tokens` /
`board_enroll_nonces` tables) is much closer to a federation credential
model than the main `auth_tokens` system is, and it was built for a
structurally similar problem — letting an external, semi-trusted consumer
(Cowork) reach a narrow slice of this platform:

- **Scoped**, not tier-blanket: `board-read` vs `board-write`, with
  `write ⊇ read` satisfaction logic.
- **TTL-bounded**, with a default that is strict rather than permissive.
- **Enrollment via single-use nonce** handed out-of-band (10-min TTL),
  which mints the actual token — a real handshake, not a shared secret.
- **Self-rotating** (`board_refresh_token`), with a documented 120s
  grace-relay retry path.
- **Revocable by label** (`board_revoke_token`).
- **Explicitly reasoned blast radius** — the read-token docstring spells
  out "leak blast radius is 'read the research surface'."

That is, in miniature, most of what a downstream-affiliate credential needs.
It is the strongest "already-exists-and-reusable" asset in this analysis.

### What does not exist and would be needed

**Cross-site identity / trust establishment.** There is no issuer concept.
`auth_tokens` answers "is this token in our table," which cannot express
"this token was issued by main campus, which we trust, for an actor at
satellite B." Needs an issuer identity, a trust root, and a verification
path. This is where ANP's DID layer would slot in — or where a far simpler
pinned-public-key model would also work.

**Data sovereignty classification.** This is the largest *policy* gap.
Today the platform has a binary-ish egress control (scrub gate: ship or
refuse) tuned for one boundary — private repo → public mirror. Federation
needs a *per-record* answer to "may this leave this site, to whom, at what
classification." CUI-adjacent content is governed by an absolute,
non-negotiable ruleset. **No data-classification schema exists on any
table.** Until someone decides which record types are site-local-forever
versus shareable-to-parent versus shareable-peer-to-peer, no federation
design can be safe — and that decision is upstream of any protocol choice.

**Cross-boundary authorization.** `require_admin` resolves a local tier
from a local token. There is no notion of a delegated or attenuated
capability — "satellite B's dispatcher may read parent's TFR feed but not
parent's watchlist, and may not re-delegate." This is capability-model
work, largely protocol-independent.

**Independently provable audit.** This is the gap most directly in tension
with the operator's stated hard constraint, and it is worth stating plainly:

> The `audit_log` table has **no tamper-evidence**. No hash chain, no
> per-row signature, no Merkle root, no DB-level append-only enforcement.
> I checked for triggers, rules, and grant restrictions on the table and
> found none — the append-only property is enforced by *convention and
> code path*, not by the database.

For a single site with one trusted operator, that is a reasonable posture.
For "provable at every location, for every operator," it is not sufficient:
a satellite site's audit log currently carries no cryptographic evidence it
was not altered after the fact, and a parent site has no way to verify a
satellite's log without trusting that satellite's operator completely.
Fixing this is **self-contained, protocol-independent, and valuable even
if federation never happens** — a per-row hash chain plus a periodically
signed checkpoint root would do it, reusing the existing agent signing key
infrastructure.

**Shared-state conflict/merge.** Watchlist entries, VIP lists, OSINT
scopes, and board threads are all single-writer today. Multi-site writes
need either a designated authority per record type or real merge semantics.
Cheapest correct answer is usually single-writer-per-record-type with
read-replication — worth deciding deliberately rather than discovering.

**Partition behavior.** The platform already has a strong fail-closed
instinct (scrub gate, manifest verification, `ANTHROPIC_FALLBACK_ENABLED`
defaulting false). Federation needs the same discipline applied to a new
question: when a satellite cannot reach the parent, does it degrade to
local-only operation, refuse, or serve stale? For a dispatch platform where
someone may be waiting on an alert, "refuse" is likely wrong and "serve
stale, clearly labeled" is likely right — but that should be an explicit
decision, and it interacts with the geometric-reasoning work (see §4).

**Affiliate revocation.** Locally this is a column flip. Federated, it is
the hard problem flagged in §1 — propagation delay, cached credentials, and
a window where a revoked affiliate may still transact. Needs a defined
maximum revocation-propagation window and a story for what happens inside it.

### What would *not* have to change

Worth stating, because these are the operator's hard commitments:

- **Local-only inference survives federation intact.** Nothing about
  site-to-site coordination requires cloud inference. Each site keeps its
  own resident model. Federation moves *data and requests* between sites,
  not inference.
- **The one-model-resident constraint survives intact.** It is a per-site
  property and federation does not touch it.

I want to be explicit since the directive asked me to flag this loudly:
**I found no aspect of ANP federation that would force relaxing either
non-negotiable.** If a future design proposal does require it, that should
be treated as a red flag about the design, not a necessary cost.

---

## 3. Gap list, ranked

Ranked by "what blocks the next real step," not by size.

| # | Gap | Classification | Size |
|---|---|---|---|
| 1 | **Data classification schema** — which records may leave a site, to whom | **policy decision first** | Medium build, large decision |
| 2 | **Tamper-evident audit** — hash chain + signed checkpoints | **needs-extension** | Medium, self-contained |
| 3 | **Cross-site identity / issuer model** | **needs-net-new** | Large |
| 4 | **Capability/delegation model** — attenuable, non-re-delegatable grants | **needs-net-new** (board tokens are the seed) | Large |
| 5 | **Revocation propagation window** | **policy decision first** | Small decision, medium build |
| 6 | **Partition/degradation behavior** | **policy decision first** | Small decision, small build |
| 7 | **Shared-state write authority per record type** | **policy decision first** | Small decision, medium build |
| 8 | **Site identity + discovery** | needs-net-new (ANP's ADP would cover this) | Medium |
| 9 | **Protocol choice: ANP vs. simpler bespoke** | **policy decision first** | — |
| 10 | Transport | **already-exists-and-reusable** — Tailscale already does authenticated site-to-site | Small |
| 11 | Per-site integrity attestation | **already-exists-and-reusable** — signed manifest already proves what code a site runs | Small |
| 12 | Egress filtering mechanism | **needs-extension** — scrub gate exists, needs per-destination policy rather than one boundary | Medium |

**Observations on this table:**

Five of the top seven are **policy decisions, not engineering**. They are
also all protocol-independent — the answers do not change based on whether
the wire protocol is ANP, A2A, or something bespoke. That is the single
most useful finding in this document: *the critical path to federation runs
through decisions the operator can make without waiting for ANP to mature.*

Items 10 and 11 are genuinely strong existing assets. Tailscale already
provides authenticated, encrypted site-to-site connectivity — for a
main-campus/satellite topology under one organization's control, it may be
sufficient transport on its own, with ANP's value being discovery and
cross-*organization* trust rather than connectivity. The signed manifest is
an unusually strong card: a site can already prove *what code it is
running*, which is a claim most federated systems cannot make.

---

## 4. Sequencing

### Genuinely parallelizable now

- **Tamper-evident audit (#2).** Zero dependency on federation or ANP.
  Strengthens the existing compliance posture standalone. Reuses the agent
  signing key already in place. Best first move if any federation work
  starts.
- **Data classification (#1).** Pure policy work, and it is the blocker for
  everything else. Can be done on paper.
- **Decisions #5, #6, #7.** Also paper.

### Must wait

- Any ANP wire-protocol implementation. The identity layer is at MVP as of
  this month and independent analysis puts production viability at 2027+.
  Building against it now means building against a moving target with no
  ecosystem to validate interoperability with.

### Interaction with geometric reasoning Phase 2/3

Three real couplings, worth knowing before Phase 2 starts rather than after:

1. **Phase 2 (`assign_causal_associations()`) is statistical and needs
   volume.** `min_samples=5` over a `days=30` rolling window. Federation
   would eventually make cross-site correlation possible — a satellite's
   delay patterns informing the parent's causal model. That is genuinely
   attractive and is also *exactly* the kind of cross-boundary data flow
   item #1 has to rule on first. **Phase 2 should not be designed assuming
   cross-site data will be available**, or it will need rework.

2. **Phase 3 (`assign_clusters()`) is graph community detection.** Cluster
   membership spanning sites is a shared-state-with-merge-semantics problem
   (#7) in its purest form. If federation is genuinely wanted later, Phase
   3's edge storage should carry a site-of-origin field from the start.
   **That is a cheap change now and an expensive migration later** — a
   one-column addition to `semantic_note_derivations` that costs nothing if
   federation never happens.

3. **Partition behavior (#6) has a concrete geometric-reasoning instance.**
   `compile_layer()` already does wholesale delete-and-recompute per `kind`.
   In a federated world, a partition during recompute could drop a peer's
   contributed edges and not restore them. Phase 2/3 design should keep
   locally-derived and externally-derived edges distinguishable — same
   one-column fix as above.

### Suggested shape, if the operator wants a next step

Not a recommendation to start, just the lowest-regret ordering if it does:

1. Decide #1 (data classification) on paper. Everything else waits on it.
2. Build #2 (tamper-evident audit). Valuable standalone, federation or not.
3. Add the site-of-origin column during Phase 2/3 rather than after.
4. Re-evaluate ANP maturity in ~2 quarters. Concrete signals to watch:
   `did:wba` moving past draft; the SDK's DID layer moving past MVP; any
   named third-party production deployment; the W3C group moving beyond
   Community Group status.
5. Only then choose the wire protocol — by which point the platform would
   be federation-*ready* and the protocol becomes a swappable detail rather
   than a foundational bet.

---

## Sources

- [A Survey of Agent Interoperability Protocols: MCP, ACP, A2A, and ANP](https://arxiv.org/html/2505.02279v1)
- [AgentNetworkProtocol — GitHub](https://github.com/agent-network-protocol/AgentNetworkProtocol)
- [Agent Network Protocol Technical White Paper (arXiv 2508.00007)](https://arxiv.org/abs/2508.00007)
- [Agent Network Protocol White Paper — W3C CG draft](https://w3c-cg.github.io/ai-agent-protocol/)
- [W3C AI Agent Protocol Community Group](https://www.w3.org/groups/cg/agentprotocol/)
- [ANP Agent Description Protocol Specification](https://agent-network-protocol.com/specs/agent-description.html)
- [did:wba — A Web-based Decentralized Identifier](https://agent-network-protocol.com/blogs/posts/did-wba-intro)
- [Agent Interoperability Protocols 2026: MCP, A2A, ACP and the Path to Convergence — Zylos Research](https://zylos.ai/research/2026-03-26-agent-interoperability-protocols-mcp-a2a-acp-convergence/)
- [Security Threat Modeling for Emerging AI-Agent Protocols (arXiv 2602.11327)](https://arxiv.org/abs/2602.11327) — *cited as a gap; ANP-specific findings not successfully extracted*
- [Supervised and revocable decentralized identity privacy protection scheme](https://sands.edpsciences.org/articles/sands/full_html/2024/01/sands20240022/sands20240022.html)
- [A Survey on Decentralized Identifiers and Verifiable Credentials](https://arxiv.org/pdf/2402.02455)
