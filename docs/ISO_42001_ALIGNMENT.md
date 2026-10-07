# ISO/IEC 42001 Alignment: [operator LLC] / CTDI

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.

Standalone reference for a client, partner or auditor. The short version sits
in `COMPLIANCE_SECURITY.md` §4.

---

## Status, stated plainly

**CTDI (the corporatetraveldc dispatch platform) is not ISO/IEC 42001
certified. [operator LLC], LLC has not undergone an accredited
ISO/IEC 42001 audit.** No claim of certification is made here or in any
client-facing material.

Certification needs an accredited body to run a two-stage audit
(documentation review, then operational effectiveness with interviews and
evidence) and annual surveillance audits over a three-year cycle. None of
that has happened.

## What this document claims instead

The platform was built with several Annex A control areas in mind, so an
operator adopting it starts closer to a certifiable posture than with a
platform built without them. This document calls that **"compliance-adjacent"**.
It is the only claim made.

---

## Control-area mapping (Annex A.2 -- A.10)

Annex A titles below are given from the published structure of
ISO/IEC 42001:2023. [UNVERIFIED against the standard text: the titles are not
checkable from this deployment; confirm against a licensed copy before using
this table in a proposal.]

| Control area | What exists today | Assessment |
| :--- | :--- | :--- |
| **A.2 -- Policies related to AI** | No standalone, management-approved AI policy. Rules that act as policy are enforced in code and in `docs/DESIGN-PRINCIPLES.md` (local-inference default, CUI rules). | **Gap.** The substance exists; the document does not. |
| **A.3 -- Internal organization** | One human operator. Since 2026-10-04 the AI agents run as separate Unix accounts (`ctdc-agent-anthropic-claude`, `ctdc-agent-anthropic-cowork`, `ctdc-agent-llama`, plus two preloaded) with no sudo and read-only repository access; an agent can request but never approve a gated action (approval = a human's SSH signature, requester != approver, `src/common/governance.py`); agents are kill-able by signed kill orders with quorum rules (`scripts/kill-order.sh`, `scripts/team-liveness.sh`). | **Partial.** Human-vs-agent separation of duties is implemented in code; there is no second human, so no human-to-human segregation, no AI risk owner distinct from the builder/operator, no governance committee. |
| **A.4 -- Resources for AI systems** | Evidenced resource guardrails: per-container memory/CPU caps, the inference server's own cgroup limits (`CPUQuota=200%`, `MemoryMax=8448M`, no swap), a thermal/load guard with an incident history (`GUARDRAILS_JUSTIFICATION.md`). Network bandwidth is **not** guarded (SWIM ingest unscoped, ~240 GiB/day in early October 2026). | **Aligned** for compute/memory/thermal; **gap** for network. |
| **A.5 -- Assessing impacts of AI systems** | No documented impact-assessment method. Decisions are made case by case by the operator, sometimes with blind adversarial reviews (e.g. the 2026-10-04 safety + code-review duel recorded in `docs/AGENT_SEGMENTATION.md`), but not to a written method. | **Gap.** (The previous revision mislabelled this row "Data for AI systems" and filled it with responsibility-allocation content, which belongs under A.3.) |
| **A.6 -- AI system life cycle** | A consistent practice: changes staged, then a signed whole-tree manifest and signed commit by the operator, root-run code installed only from verified copies, images rebuilt from the signed tree and gated before restart (`scripts/stack-refresh.sh`), skills granted per agent from a signed catalog (`scripts/skill-grants.sh`). | **Partial.** Real and enforced in tooling; not written up as a lifecycle policy an auditor could review on its own. |
| **A.7 -- Data for AI systems** | No query or model input/output leaves the box by default: inference is one local llama.cpp server on the tailnet IP. CUI radio data (SHARES/HEARS/HEART) is never in code, configs, exports or documents. Workspace writes from agents pass the CUI/PII scrub gate. The audit log is hash-chained on Postgres and archived with signatures rather than deleted (`COMPLIANCE_SECURITY.md` §3). | **Aligned.** |
| **A.8 -- Information for interested parties** | This document and `COMPLIANCE_SECURITY.md` are the disclosure. No formal communications process. | **Partial.** |
| **A.9 -- Use of AI systems** | Deterministic fallback when local inference is down; no silent failover to a cloud provider. The Anthropic fallback in `src/common/llm.py` is closed by `ANTHROPIC_FALLBACK_ENABLED=false` (live `dispatch.env`) and per-call `allow_anthropic=False`. Pre-flight thermal/load checks gate inference (`preflight_cool_launch_if_needed()`). Human approval for gated actions is a signature, not a tap (`scripts/approve.sh`); agents draft into the shared workspace but have no publish, overwrite or delete route. | **Aligned.** |
| **A.10 -- Third-party and customer relationships** | Local inference removes model-vendor data handling for operational data. Cloud agents (Anthropic, OpenAI) can reach the platform only through the agent gateway, per-agent OAuth, one MCP endpoint per identity, revocable from our side and killable in one command (`scripts/agent-gateway.sh kill-all`). Container networking is per-container opt-in. | **Aligned**, with the gateway as the one deliberate third-party surface. |

## The gap list

Four things would need to exist before an audit could be attempted; none
exists today:

1. A formal, management-approved AI policy consolidating the rules already in
   code and `DESIGN-PRINCIPLES.md`.
2. Defined AI-governance roles beyond "the operator does everything"
   (human-vs-agent separation now exists in code; human roles do not).
3. A documented AI risk / impact-assessment method (A.5).
4. A management-review cadence.

## Net position

CTDI and [operator LLC] are not ISO/IEC 42001 certified and do not
claim to be. The hard technical work -- data handling, vendor isolation,
resource governance, signed human approval -- is largely done; the
management-system paperwork is not.

Update this document if an audit is started, a gap closes (move it into the
table with evidence), or the architecture changes (for example, enabling the
cloud LLM fallback means re-reviewing A.7, A.9 and A.10).

*Related: `COMPLIANCE_SECURITY.md` §4, `DESIGN-PRINCIPLES.md`,
`GUARDRAILS_JUSTIFICATION.md`, `AGENT_SEGMENTATION.md`.*

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-security.md`.


### ISO/IEC 42001 Alignment: [operator LLC] / CTDI

~~**This is a standalone reference document.** For the shorter summary embedded in the platform's broader compliance datasheet, see `COMPLIANCE_SECURITY.md` §4. This page exists to give a client, partner, or auditor the full picture in one place -- the complete control-area mapping, the honest gap list, and the exact language [operator LLC] uses to describe this in a pitch or proposal.~~


### ISO/IEC 42001 Alignment: [operator LLC] / CTDI › Status, stated plainly

~~**CTDI (the corporatetraveldc dispatch platform) is not ISO/IEC 42001 certified. [operator LLC], LLC has not undergone an accredited ISO/IEC 42001 audit.** No claim of certification is made anywhere in this document, in the platform's marketing materials, or in any client-facing communication. If that framing ever needs restating in a specific proposal or contract, this is the sentence to reuse.~~

~~Certification under ISO/IEC 42001 requires an accredited third-party certification body to complete a two-stage audit -- first a documentation review, then an operational-effectiveness evaluation involving staff interviews and evidence collection -- followed by annual surveillance audits over a three-year certification cycle. That process has not happened here. It is a real, resource-intensive undertaking, and skipping it is not a gap this document tries to paper over.~~

**~~What this document actually claims instead~~** *(former heading)*


### ISO/IEC 42001 Alignment: [operator LLC] / CTDI › What this document actually claims instead

~~CTDI's architecture and [operator LLC]' operating practices were built around several of ISO/IEC 42001's Annex A control objectives from early in the platform's development -- independent of, and prior to, this document being written. That means an operator adopting CTDI today starts from a materially stronger position than one adopting a platform with no such framework in mind, should they or [operator LLC] later choose to pursue certification. This document calls that position **"compliance-adjacent"**: the hard technical and operational work that a real audit would evaluate is already substantially done, even though the audit itself has not occurred.~~

~~This is a narrower and more defensible claim than "we follow ISO 42001," and it is the only claim this document makes.~~

**~~Full control-area mapping (Annex A.2 -- A.10)~~** *(former heading)*


### ISO/IEC 42001 Alignment: [operator LLC] / CTDI › Full control-area mapping (Annex A.2 -- A.10)

~~ISO/IEC 42001's Annex A organizes controls into nine areas. The table below addresses all nine honestly -- including the ones where the answer is "not yet formalized" -- rather than only listing the areas that look good.~~

| ~~Control area~~ | ~~What exists today~~ | ~~Assessment~~ |
| :--- | :--- | :--- |
| ~~**A.2 -- Policies related to AI**~~ | ~~No standalone, board-approved "AI Policy" document exists. Operating rules that function like policy are enforced in code and in `DESIGN-PRINCIPLES.md` (e.g., local-inference-only default, CUI handling rules), but they have not been consolidated into a single top-management-owned policy artifact.~~ | ~~**Gap.** Straightforward to close -- the substance exists, the document doesn't.~~ |
| ~~**A.3 -- Internal organization**~~ | ~~[operator LLC] is a single-operator business. There is no separate AI governance committee, no named AI risk owner distinct from the platform's builder/operator, and no segregation-of-duties structure.~~ | ~~**Gap, structural.** This is a function of company size, not neglect -- closing it fully would mean growing the organization, not just writing a document. Worth stating honestly rather than implying a governance structure that doesn't exist.~~ |
| ~~**A.4 -- Resources for AI systems**~~ | ~~Real, evidenced resource guardrails exist for every AI-adjacent process: network bandwidth caps, memory/CPU Quadlet limits, thermal monitoring with a documented incident history (see `GUARDRAILS_JUSTIFICATION.md` and this session's thermal-root-cause and sudo-approval-gate work). These aren't theoretical ceilings -- they were tuned against real dated incidents.~~ | ~~**Aligned.** This is the platform's strongest control area, and it's backed by operational history, not just a config file.~~ |
| ~~**A.5 -- (heading needs checking against the standard text)**~~ | ~~⚠️ *Internal inconsistency flagged 2026-08-23, not resolved: this row is titled "Data for AI systems" — the same title the A.7 row below carries — while its parenthetical says A.5 "covers roles/responsibilities" and its content is about responsibility allocation. Two rows cannot both be "Data for AI systems," so at least the heading here is wrong. This is a claim about the text of ISO/IEC 42001:2023 Annex A, which is not verifiable from this deployment, so it is flagged rather than rewritten.* **NEEDS OPERATOR ACTION:** check A.5's real title against the standard and retitle this row. A control-area mapping that mislabels a control area is the kind of error an auditor would find first. Responsibility for the platform's AI-adjacent decisions currently sits entirely with the operator (the operator). No delegation or dual-control exists.~~ | ~~**Gap**, same root cause as A.3.~~ |
| ~~**A.6 -- AI system life cycle**~~ | ~~Development follows a consistent, repeatable pattern documented throughout this session's own work: build → syntax-check → containerized build → restart → live-log verification → staged (never auto-committed) → memory-recorded. This is an informal but real and consistently-applied lifecycle discipline.~~ | ~~**Partially aligned.** The practice is real and disciplined; it has not been written up as a formal lifecycle policy document a certification auditor could review independent of watching the operator work.~~ |
| ~~**A.7 -- Data for AI systems**~~ | ~~No operator query, model input, or model output is ever sent to any external party by default -- local-only inference is a hard architectural default (`DESIGN-PRINCIPLES.md` §2; host llama.cpp since 2026-08-27, previously Ollama). CUI-classified radio data (SHARES/HEARS/HEART) is handled under an explicit, non-negotiable ruleset: never in code, configs, exports, or documents, even password-protected, with an append-only 90-day audit log that never leaves the device.~~ | ~~**Aligned, strongly.** This is real, load-bearing, and has been enforced consistently across every session touching CUI-adjacent work.~~ |
| ~~**A.8 -- Information for interested parties**~~ | ~~This document and `COMPLIANCE_SECURITY.md` are themselves the primary artifact here -- an attempt at clear, honest disclosure to clients/partners about what the platform does and does not do. There is no formal external-communications policy beyond that.~~ | ~~**Partially aligned.** The disclosure exists and is honest; it hasn't been formalized into a recurring communications process.~~ |
| ~~**A.9 -- Responsible use of the AI system**~~ | ~~Deterministic fallback is required when local inference is unavailable -- the system does not silently fail over to a cloud provider if the local inference server is down. An Anthropic fallback integration does exist in code (`src/common/llm.py`) but is disabled at two independent gates: `ANTHROPIC_FALLBACK_ENABLED=false` in `dispatch.env` (since 2026-08-12) and per-call `allow_anthropic=False` passed by the brief skills -- so zero cloud calls occur; enabling it would be an explicit operator opt-in. Pre-flight/mid-flight thermal and load checks gate inference (`preflight_cool_launch_if_needed()`, `src/common/llm.py:529` — line numbers drift, grep the symbol), and a human-approval gate exists for higher-risk automated actions (`scripts/sudo-approval-gate.sh`): since 2026-10-04 approval is a human's SSH signature over the exact request with a separate passphrase-protected approval key (`scripts/approve.sh`, `src/common/governance.py`); the `approval-gate` ntfy push only notifies and offers Deny, so no tap or link can allow anything.~~ | ~~**Aligned.** Concrete, tested guardrails, not a policy statement. Re-verified live 2026-08-23: `/etc/corporatetraveldc/dispatch.env:200` reads `ANTHROPIC_FALLBACK_ENABLED=false`; `scripts/sudo-approval-gate.sh` exists and is executable.~~ |
| ~~**A.10 -- Third-party / supplier relationships**~~ | ~~Local-only inference removes most of the AI supply-chain risk (model-vendor data handling, training-data exposure, vendor outage dependency) that this control area is largely designed to help organizations manage. Container network isolation (air-gapped by default, scoped opt-ins rather than host networking) further limits any third-party attack surface.~~ | ~~**Aligned.** A structural property of the architecture, not a policy commitment that could lapse.~~ |

**~~The honest gap list, stated once and not softened~~** *(former heading)*


### ISO/IEC 42001 Alignment: [operator LLC] / CTDI › The honest gap list, stated once and not softened

~~Four things would need to exist before a real ISO/IEC 42001 certification audit could be attempted, and none of them exist today:~~

1. ~~**A formal, top-management-approved AI policy document** -- consolidating the informal rules already enforced in code and in `DESIGN-PRINCIPLES.md` into a single reviewable artifact.~~
2. ~~**Defined AI-governance roles and responsibilities** -- distinct from "the operator does everything," which is the accurate current state for a single-person business.~~
3. ~~**A documented AI risk / impact-assessment methodology** -- a repeatable process for evaluating risk before a new AI-adjacent feature ships, rather than the current ad-hoc judgment calls (which have been sound in practice, per this session's own catch-and-fix history, but aren't written down as a method).~~
4. ~~**A management-review cadence** -- a recurring, scheduled review of the AI management system's performance, as opposed to continuous informal iteration.~~

~~None of these are difficult to build on top of what already exists technically -- the underlying practices they would formalize are largely already there, per the mapping above. But they are real gaps, not paperwork technicalities, and this document does not describe them as already closed anywhere else.~~

**~~Net position, for a client or partner conversation~~** *(former heading)*


### ISO/IEC 42001 Alignment: [operator LLC] / CTDI › Net position, for a client or partner conversation

~~CTDI and [operator LLC] are not ISO/IEC 42001 certified, and do not claim to be. The platform was built using ISO/IEC 42001's control areas as design guardrails from early in its development -- which means an operator adopting it starts substantively closer to a certifiable posture than one adopting a platform built without that framework in mind. That is what "compliance-adjacent from day one" means in this document: the hard technical work -- data handling, vendor isolation, resource governance, responsible-use guardrails -- is already done. It does not mean a certificate exists, and the gap list above is the honest accounting of what pursuing one would still require.~~

~~This document should be updated if: an accredited audit is undertaken (update the Status section immediately, don't wait for the result), any of the four gaps above is closed (move that item from the gap list into the mapping table with evidence), or the control-area mapping changes because the platform's architecture changes (e.g., if the existing cloud-LLM fallback is ever enabled, A.7/A.9/A.10 all need re-review, not just a footnote).~~

~~*Related: `COMPLIANCE_SECURITY.md` §4 (summary version of this mapping, embedded in the broader compliance datasheet), `DESIGN-PRINCIPLES.md` (the enforced rules this document references), `GUARDRAILS_JUSTIFICATION.md` (the evidence behind the A.4 resource-guardrail claims).*~~
