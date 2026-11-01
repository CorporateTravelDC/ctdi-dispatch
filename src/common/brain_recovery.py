"""Acceptance predicates for real compiler recovery, independent of drift accounting."""
from datetime import datetime, timezone


class ProofFailed(RuntimeError):
    pass


def count(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ProofFailed("recovery evidence has an invalid counter") from exc


def timestamp(value):
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if out.tzinfo is None:
            raise ValueError("unlabelled time")
        return out.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ProofFailed("recovery evidence has no valid labelled timestamp") from exc


def successful_run(unit: dict, started_at: str):
    if unit.get("Result") != "success" or str(unit.get("ExecMainStatus")) != "0":
        raise ProofFailed("compiler unit did not complete successfully")
    if unit.get("ActiveState") not in ("inactive", "active") or unit.get("SubState") not in ("dead", "exited"):
        raise ProofFailed("compiler unit is not in a completed state")
    if timestamp(unit.get("started_at")) < timestamp(started_at):
        raise ProofFailed("unit success predates this validation run")


def semantic_passed(before: dict, after: dict, unit: dict, started_at: str):
    successful_run(unit, started_at)
    seq = count(after.get("compile_seq"))
    if seq <= count(before.get("compile_seq")):
        raise ProofFailed("successful semantic compile sequence did not advance")
    if timestamp(after.get("compiled_at")) < timestamp(started_at):
        raise ProofFailed("semantic compilation timestamp did not advance into this run")
    if after.get("ledger_ok") is not True or count(after.get("ledger_compile_seq")) != seq:
        raise ProofFailed("semantic metadata does not match a successful committed ledger row")
    if count(after.get("assignments")) <= 0:
        raise ProofFailed("semantic compilation produced no concept assignments")


def graph_passed(before: dict, after: dict, semantic: dict, unit: dict, started_at: str):
    successful_run(unit, started_at)
    generated = timestamp(after.get("generated_at"))
    if generated < timestamp(started_at) or generated <= timestamp(before.get("generated_at")):
        raise ProofFailed("served graph did not advance into this validation run")
    if count(after.get("semantic_compile_seq")) != count(semantic.get("compile_seq")):
        raise ProofFailed("served graph does not contain the verified semantic revision")
    if timestamp(after.get("semantic_compiled_at")) != timestamp(semantic.get("compiled_at")):
        raise ProofFailed("served graph semantic timestamp does not match the committed layer")
    if generated < timestamp(semantic.get("compiled_at")):
        raise ProofFailed("served graph predates its semantic compilation")
    if count(after.get("node_count")) <= 0 or count(after.get("semantic_note_links")) <= 0:
        raise ProofFailed("served graph is missing curated nodes or semantic note links")
