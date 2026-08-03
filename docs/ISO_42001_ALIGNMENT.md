# ISO/IEC 42001 Alignment: [operator LLC] / CTDI

**This is a standalone reference document.** For the shorter summary embedded in the platform's broader compliance datasheet, see `COMPLIANCE_SECURITY.md` §4. This page exists to give a client, partner, or auditor the full picture in one place -- the complete control-area mapping, the honest gap list, and the exact language [operator LLC] uses to describe this in a pitch or proposal.

---

## Status, stated plainly

**CTDI (the corporatetraveldc dispatch platform) is not ISO/IEC 42001 certified. [operator LLC], LLC has not undergone an accredited ISO/IEC 42001 audit.** No claim of certification is made anywhere in this document, in the platform's marketing materials, or in any client-facing communication. If that framing ever needs restating in a specific proposal or contract, this is the sentence to reuse.

Certification under ISO/IEC 42001 requires an accredited third-party certification body to complete a two-stage audit -- first a documentation review, then an operational-effectiveness evaluation involving staff interviews and evidence collection -- followed by annual surveillance audits over a three-year certification cycle. That process has not happened here. It is a real, resource-intensive undertaking, and skipping it is not a gap this document tries to paper over.

## What this document actually claims instead

CTDI's architecture and [operator LLC]' operating practices were built around several of ISO/IEC 42001's Annex A control objectives from early in the platform's development -- independent of, and prior to, this document being written. That means an operator adopting CTDI today starts from a materially stronger position than one adopting a platform with no such framework in mind, should they or [operator LLC] later choose to pursue certification. This document calls that position **"compliance-adjacent"**: the hard technical and operational work that a real audit would evaluate is already substantially done, even though the audit itself has not occurred.

This is a narrower and more defensible claim than "we follow ISO 42001," and it is the only claim this document makes.

---

## Full control-area mapping (Annex A.2 -- A.10)

ISO/IEC 42001's Annex A organizes controls into nine areas. The table below addresses all nine honestly -- including the ones where the answer is "not yet formalized" -- rather than only listing the areas that look good.

| Control area | What exists today | Assessment |
| :--- | :--- | :--- |
| **A.2 -- Policies related to AI** | No standalone, board-approved "AI Policy" document exists. Operating rules that function like policy are enforced in code and in `DESIGN-PRINCIPLES.md` (e.g., local-inference-only default, CUI handling rules), but they have not been consolidated into a single top-management-owned policy artifact. | **Gap.** Straightforward to close -- the substance exists, the document doesn't. |
| **A.3 -- Internal organization** | [operator LLC] is a single-operator business. There is no separate AI governance committee, no named AI risk owner distinct from the platform's builder/operator, and no segregation-of-duties structure. | **Gap, structural.** This is a function of company size, not neglect -- closing it fully would mean growing the organization, not just writing a document. Worth stating honestly rather than implying a governance structure that doesn't exist. |
| **A.4 -- Resources for AI systems** | Real, evidenced resource guardrails exist for every AI-adjacent process: network bandwidth caps, memory/CPU Quadlet limits, thermal monitoring with a documented incident history (see `GUARDRAILS_JUSTIFICATION.md` and this session's thermal-root-cause and sudo-approval-gate work). These aren't theoretical ceilings -- they were tuned against real dated incidents. | **Aligned.** This is the platform's strongest control area, and it's backed by operational history, not just a config file. |
| **A.5 -- Data for AI systems** | *(Note: A.5 in the 2023 standard covers roles/responsibilities; data-specific controls sit primarily under A.7 below -- both are addressed here for completeness.)* Responsibility for the platform's AI-adjacent decisions currently sits entirely with the operator (the operator). No delegation or dual-control exists. | **Gap**, same root cause as A.3. |
| **A.6 -- AI system life cycle** | Development follows a consistent, repeatable pattern documented throughout this session's own work: build → syntax-check → containerized build → restart → live-log verification → staged (never auto-committed) → memory-recorded. This is an informal but real and consistently-applied lifecycle discipline. | **Partially aligned.** The practice is real and disciplined; it has not been written up as a formal lifecycle policy document a certification auditor could review independent of watching the operator work. |
| **A.7 -- Data for AI systems** | No operator query, model input, or model output is ever sent to any external party by default -- Ollama-only local inference is a hard architectural default (`DESIGN-PRINCIPLES.md` §2). CUI-classified radio data (SHARES/HEARS/HEART) is handled under an explicit, non-negotiable ruleset: never in code, configs, exports, or documents, even password-protected, with an append-only 90-day audit log that never leaves the device. | **Aligned, strongly.** This is real, load-bearing, and has been enforced consistently across every session touching CUI-adjacent work. |
| **A.8 -- Information for interested parties** | This document and `COMPLIANCE_SECURITY.md` are themselves the primary artifact here -- an attempt at clear, honest disclosure to clients/partners about what the platform does and does not do. There is no formal external-communications policy beyond that. | **Partially aligned.** The disclosure exists and is honest; it hasn't been formalized into a recurring communications process. |
| **A.9 -- Responsible use of the AI system** | Deterministic fallback is required when local inference is unavailable -- the system does not silently fail over to a cloud provider if Ollama is down or paused. Any cloud LLM integration would have to be an explicit, operator-controlled opt-in; none exists today. Pre-flight/mid-flight thermal and load checks gate inference (`ollama_preflight_cool_launch_gate`), and a human-approval gate exists for higher-risk automated actions (`sudo_approval_gate`, ntfy Allow/Deny). | **Aligned.** Concrete, tested guardrails, not a policy statement. |
| **A.10 -- Third-party / supplier relationships** | Local-only inference removes most of the AI supply-chain risk (model-vendor data handling, training-data exposure, vendor outage dependency) that this control area is largely designed to help organizations manage. Container network isolation (air-gapped by default, scoped opt-ins rather than host networking) further limits any third-party attack surface. | **Aligned.** A structural property of the architecture, not a policy commitment that could lapse. |

## The honest gap list, stated once and not softened

Four things would need to exist before a real ISO/IEC 42001 certification audit could be attempted, and none of them exist today:

1. **A formal, top-management-approved AI policy document** -- consolidating the informal rules already enforced in code and in `DESIGN-PRINCIPLES.md` into a single reviewable artifact.
2. **Defined AI-governance roles and responsibilities** -- distinct from "the operator does everything," which is the accurate current state for a single-person business.
3. **A documented AI risk / impact-assessment methodology** -- a repeatable process for evaluating risk before a new AI-adjacent feature ships, rather than the current ad-hoc judgment calls (which have been sound in practice, per this session's own catch-and-fix history, but aren't written down as a method).
4. **A management-review cadence** -- a recurring, scheduled review of the AI management system's performance, as opposed to continuous informal iteration.

None of these are difficult to build on top of what already exists technically -- the underlying practices they would formalize are largely already there, per the mapping above. But they are real gaps, not paperwork technicalities, and this document does not describe them as already closed anywhere else.

---

## Net position, for a client or partner conversation

CTDI and [operator LLC] are not ISO/IEC 42001 certified, and do not claim to be. The platform was built using ISO/IEC 42001's control areas as design guardrails from early in its development -- which means an operator adopting it starts substantively closer to a certifiable posture than one adopting a platform built without that framework in mind. That is what "compliance-adjacent from day one" means in this document: the hard technical work -- data handling, vendor isolation, resource governance, responsible-use guardrails -- is already done. It does not mean a certificate exists, and the gap list above is the honest accounting of what pursuing one would still require.

This document should be updated if: an accredited audit is undertaken (update the Status section immediately, don't wait for the result), any of the four gaps above is closed (move that item from the gap list into the mapping table with evidence), or the control-area mapping changes because the platform's architecture changes (e.g., if a cloud LLM integration is ever added as an opt-in, A.7/A.9/A.10 all need re-review, not just a footnote).

---

*Related: `COMPLIANCE_SECURITY.md` §4 (summary version of this mapping, embedded in the broader compliance datasheet), `DESIGN-PRINCIPLES.md` (the enforced rules this document references), `GUARDRAILS_JUSTIFICATION.md` (the evidence behind the A.4 resource-guardrail claims).*
