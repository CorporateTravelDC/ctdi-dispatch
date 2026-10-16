---
name: "adversarial-sweep-docs-first"
description: "Documentation refresh first, then the pen test, then an attack on the refreshed docs, then one supervisory pass. Trigger on \"docs-first sweep\", \"refresh then pen test\", \"docs first\"."
---

# adversarial-sweep-docs-first

One of the four orderings of the two adversarial base skills, `adversarial-pentest` and `adversarial-doc-refresh` (operator, 2026-10-16: "we have it both ways"). The base skills hold the method; this skill holds only the sequence and the hand-off rules.

## Sequence

1. `adversarial-doc-refresh` (stages 0-7). Output: rewritten docs + claim register.
2. `adversarial-pentest` (stages 0-7), receiving the claim register so every documented security statement is a target to test.
3. Attack the refreshed docs with the pen test's findings in hand: every REFUTED statement goes back to the rewrite stage until two dry rounds.
4. Supervisory pass over both registers.
5. Final checkpoint (counts, decisions, investor regeneration call).

## How a sweep runs

- Each stage is the full skill it names (`adversarial-pentest`, `adversarial-doc-refresh`), with its own checkpoints, boundaries, exclusion list and verdict labels. Nothing here shortens them.
- Hand-off rule: a stage receives only the previous stage's VERIFIED output (the findings register with verdict labels, or the rewritten docs plus their claim register), never its reasoning, drafts or agent transcripts. Fresh agents at every stage.
- "Attack the docs" (a pen test of documents) means: fresh refuters take every statement that stands in the rewritten docs and try to disprove it against the live system and the code, with the pen-test boundaries (read-mostly, 1-2 requests, no writes), labelling REFUTED / HOLDS / NEEDS-HUMAN-REVIEW; every REFUTED goes back to the doc-refresh rewrite stage.
- One supervisory pass closes every sweep: a single fresh agent, fed only the final refute and findings logs, re-derives each item from live state and source (`CONFIRMED-BY-INDEPENDENT-RECHECK` / `COULD-NOT-REPRODUCE` / `NEEDS-HUMAN-REVIEW` / `RESOLVED-SINCE-REPORT`). It is never skipped.
- Investor materials (`docs/investor-materials/**`) may be regenerated only at the end of a sweep, only from claims that carry a `CONFIRMED` or `HOLDS` label, only with the "what NOT to claim" list applied, and only when the operator says so at the final checkpoint. Rendering: `src/second_brain/doc_generation.py` (docx/pptx/pdf), then `scripts/scrub-public-tree.py` verification.
- Committed is not running: a sweep starts only after the running images match the signed tree, and a sweep whose middle stages changed code waits for the signed rollout before the stage that re-validates it.
- Every stage writes its output to the run directory so an interrupted sweep resumes at the last checkpoint. The operator sees every delegation prompt before dispatch. Agents draft; they never publish, sign, commit or approve.
- Hand-off: operator session -> guard worktree -> `scripts/relay-preflight.sh` -> relay on PASS; agent account -> workspace contribution. Record the run in the vault.
