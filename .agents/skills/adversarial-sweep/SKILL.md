---
name: "adversarial-sweep"
description: "Pen test first, then a documentation refresh fed only the pen test's verified claims, then one supervisory pass. Trigger on \"adversarial sweep\", \"one-two punch\", \"pen test then docs\", \"full sweep\"."
---

# adversarial-sweep

One of the four orderings of the two adversarial base skills, `adversarial-pentest` and `adversarial-doc-refresh` (operator, 2026-10-16: "we have it both ways"). The base skills hold the method; this skill holds only the sequence and the hand-off rules.

## Sequence

1. `adversarial-pentest` (stages 0-7). Output: the findings register with labels, the investor-grade claim/proof/caveat section.
2. `adversarial-doc-refresh` (stages 0-7). Its truth set is built from the system as always, AND it receives the pen test's register so every security-related statement in the docs is written from a CONFIRMED finding or struck.
3. Supervisory pass over both registers.
4. Final checkpoint: counts, open NEEDS-OPERATOR-DECISION items, and the operator's call on regenerating investor materials.

## How a sweep runs

- Each stage is the full skill it names (`adversarial-pentest`, `adversarial-doc-refresh`), with its own checkpoints, boundaries, exclusion list and verdict labels. Nothing here shortens them.
- Hand-off rule: a stage receives only the previous stage's VERIFIED output (the findings register with verdict labels, or the rewritten docs plus their claim register), never its reasoning, drafts or agent transcripts. Fresh agents at every stage.
- "Attack the docs" (a pen test of documents) means: fresh refuters take every statement that stands in the rewritten docs and try to disprove it against the live system and the code, with the pen-test boundaries (read-mostly, 1-2 requests, no writes), labelling REFUTED / HOLDS / NEEDS-HUMAN-REVIEW; every REFUTED goes back to the doc-refresh rewrite stage.
- One supervisory pass closes every sweep: a single fresh agent, fed only the final refute and findings logs, re-derives each item from live state and source (`CONFIRMED-BY-INDEPENDENT-RECHECK` / `COULD-NOT-REPRODUCE` / `NEEDS-HUMAN-REVIEW` / `RESOLVED-SINCE-REPORT`). It is never skipped.
- Investor materials (`docs/investor-materials/**`) may be regenerated only at the end of a sweep, only from claims that carry a `CONFIRMED` or `HOLDS` label, only with the "what NOT to claim" list applied, and only when the operator says so at the final checkpoint. Rendering: `src/second_brain/doc_generation.py` (docx/pptx/pdf), then `scripts/scrub-public-tree.py` verification.
- Committed is not running: a sweep starts only after the running images match the signed tree, and a sweep whose middle stages changed code waits for the signed rollout before the stage that re-validates it.
- Every stage writes its output to the run directory so an interrupted sweep resumes at the last checkpoint. The operator sees every delegation prompt before dispatch. Agents draft; they never publish, sign, commit or approve.
- Hand-off: operator session -> guard worktree -> `scripts/relay-preflight.sh` -> relay on PASS; agent account -> workspace contribution. Record the run in the vault.
