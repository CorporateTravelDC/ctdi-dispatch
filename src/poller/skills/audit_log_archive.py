"""poller.skills.audit_log_archive -- signed, off-box archival for audit_log.

Replaces bare deletion. The retention decision (operator, 2026-09-21) is that
audit rows are NEVER pruned in place: anything past the retention horizon is
checkpointed, signed, uploaded off-box, and replaced on-device by a condensed
signed stub. The stub keeps the audit question answerable -- which actors,
which privilege tiers, over what window, how many events, and the digest
proving the exported detail is authentic -- without keeping every row
resident. Deleting audit rows with no signed successor is the one thing this
must never do.

Ordering is lifted deliberately from poller/skills/flight_events_cleanup.py,
which already solved the same problem for flight_events: bounded batches,
oldest first, upload to the Nextcloud vault over WebDAV, and delete ONLY
after that batch's upload is confirmed. That property is why this is safe to
run unattended -- a failed upload leaves rows live to be retried next run,
never half-archived.

Off-box destination is WebDAV/Nextcloud, not a NAS: no NAS is mounted on this
box and no rsync/scp path exists, whereas WebDAV is already credentialed and
already carries archives/flight_events. Verified 2026-09-21.

Differences from the flight_events sibling, all required by the audit use case:
  * SHA-256 of each tarball, recorded so the off-box copy can be proven
    authentic later.
  * A GPG detached signature over the checkpoint content, using the AGENT
    signing key (security/signing.env), same key sign-manifest.sh uses.
  * Rows are hash-chained (pg_schema/0062). The chain head at the archived
    boundary is captured BEFORE deletion, so the surviving chain remains
    verifiable across the gap via audit_checkpoints.
  * A condensed actor/action summary is written to audit_archive_stubs.

SR-2 exemption: inputs are inherently time-bounded (a retention cutoff), so
there is nothing content-hashable to gate on.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import os
import subprocess
import tarfile
from collections import defaultdict
from datetime import datetime, timezone

from common import config, db
from common.sr1_log import log_usage
from second_brain import webdav_client

log = logging.getLogger(__name__)
SKILL_NAME = "audit-log-archive"

# Configurable horizon. Default 90d matches the retention the bare
# prune_audit_log() used to enforce, so switching to this skill preserves the
# on-device footprint while adding provability.
RETENTION_DAYS = int(config.get("AUDIT_ARCHIVE_RETENTION_DAYS", "90") or 90)

# Same reasoning as flight_events_cleanup's BATCH_SIZE: keeps memory flat
# regardless of how large a first-run backlog is.
BATCH_SIZE = 1000
ARCHIVE_WEBDAV_DIR = f"{webdav_client.BUSINESS_ROOT}/archives/audit_log"


def _agent_key() -> str | None:
    """AGENT signing key fingerprint from security/signing.env.

    Deliberately the agent key, not the operator's: a signature's key id is
    itself an honest audit trail of who produced it, and this runs unattended.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "..", "security", "signing.env",
    )
    try:
        with open(os.path.normpath(path)) as fh:
            for line in fh:
                if line.startswith("AGENT_SIGNING_KEY_FINGERPRINT="):
                    return line.partition("=")[2].strip() or None
    except OSError:
        return None
    return None


def _sign(payload: str, key: str) -> str | None:
    """Detached ASCII-armored signature over payload. The agent key has no
    passphrase by design (see sign-manifest.sh --agent), so this does not
    prompt and is safe unattended."""
    try:
        res = subprocess.run(
            ["gpg", "--local-user", key, "--detach-sign", "--armor", "--output", "-"],
            input=payload.encode("utf-8"),
            capture_output=True, timeout=60,
        )
        if res.returncode != 0:
            log.error("%s: gpg signing failed -- %s", SKILL_NAME,
                      res.stderr.decode("utf-8", "replace")[:300])
            return None
        return res.stdout.decode("utf-8")
    except Exception as e:
        log.error("%s: gpg signing errored -- %s", SKILL_NAME, e)
        return None


def _tarball(rows: list[dict], stamp: str) -> bytes:
    """Gzipped JSONL inside a tar, one JSON object per line -- same shape as
    the flight_events archive so both are read back the same way."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for r in rows:
            gz.write((json.dumps(r, default=str) + "\n").encode("utf-8"))
    raw = buf.getvalue()

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=f"audit_log_{stamp}.jsonl.gz")
        info.size = len(raw)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        tar.addfile(info, io.BytesIO(raw))
    return tar_buf.getvalue()


def _summarize(rows: list[dict]) -> tuple[str, str]:
    """Condense to the two questions a stub must still answer: who acted
    (with privilege tier), and what did they do. Counts only -- the detail
    lives in the archive."""
    actors: dict[str, dict] = defaultdict(lambda: {"tier": "", "count": 0})
    actions: dict[str, int] = defaultdict(int)
    for r in rows:
        who = r.get("token_prefix") or "(none)"
        actors[who]["tier"] = r.get("tier") or actors[who]["tier"]
        actors[who]["count"] += 1
        actions[r.get("action") or "(unknown)"] += 1
    return json.dumps(actors, sort_keys=True), json.dumps(actions, sort_keys=True)


def run() -> None:
    status = "ok"
    archived = 0
    try:
        key = _agent_key()
        if not key:
            log.error("%s: no AGENT_SIGNING_KEY_FINGERPRINT -- refusing to archive "
                      "unsigned (an unsigned archive defeats the whole point)", SKILL_NAME)
            status = "no_signing_key"
            return

        cutoff = datetime.now(timezone.utc).timestamp() - RETENTION_DAYS * 86400
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        batch = 0

        while True:
            with db.conn() as c:
                rows = c.execute(
                    "SELECT * FROM audit_log WHERE event_time < ? "
                    "ORDER BY id ASC LIMIT ?", (cutoff, BATCH_SIZE)
                ).fetchall()
            rows = [dict(r) for r in rows]
            if not rows:
                break

            ids = [r["id"] for r in rows]
            lo, hi = ids[0], ids[-1]
            head = rows[-1].get("row_hash") or ""

            tar = _tarball(rows, f"{stamp}_batch{batch:03d}")
            digest = hashlib.sha256(tar).hexdigest()
            name = f"audit_log_archive_{stamp}_batch{batch:03d}.tar.gz"
            rel = f"{ARCHIVE_WEBDAV_DIR}/{name}"

            # Sign the checkpoint FACTS, not the blob: the digest binds the
            # blob, and signing a small canonical string keeps the signature
            # verifiable without re-downloading the archive.
            payload = json.dumps({
                "covers_from_id": lo, "covers_to_id": hi,
                "row_count": len(rows), "chain_head_hash": head,
                "archive_sha256": digest, "archive_uri": rel,
            }, sort_keys=True)
            sig = _sign(payload, key)
            if not sig:
                log.error("%s: signing failed on batch %d -- nothing deleted", SKILL_NAME, batch)
                status = "sign_failed"
                break

            try:
                webdav_client.put(rel, tar, content_type="application/gzip")
            except Exception as e:
                # Identical posture to flight_events_cleanup: a failed upload
                # must never be followed by a delete. Rows stay live and get
                # re-exported next run.
                log.error("%s: upload failed on batch %d (%s) -- %d row(s) remain live",
                          SKILL_NAME, batch, e, len(rows))
                status = "upload_failed"
                break

            actors_json, actions_json = _summarize(rows)
            now_iso = datetime.now(timezone.utc).isoformat()

            with db.conn() as c:
                c.execute(
                    "INSERT INTO audit_checkpoints (created_at, covers_from_id, "
                    "covers_to_id, chain_head_hash, signature, signing_key_id) "
                    "VALUES (?,?,?,?,?,?)",
                    (now_iso, lo, hi, head, sig, key))
                c.execute(
                    "INSERT INTO audit_archive_stubs (created_at, covers_from_id, "
                    "covers_to_id, covers_from_ts, covers_to_ts, row_count, actors, "
                    "actions, chain_head_hash, archive_sha256, archive_uri, signature, "
                    "signing_key_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now_iso, lo, hi, rows[0].get("event_time"), rows[-1].get("event_time"),
                     len(rows), actors_json, actions_json, head, digest, rel, sig, key))
                # Only now is deletion safe: uploaded, digested, signed, and
                # both successor records committed in this same transaction.
                c.execute("DELETE FROM audit_log WHERE id >= ? AND id <= ?", (lo, hi))

            archived += len(rows)
            log.info("%s: batch %d -- %d row(s) ids %d..%d -> %s (sha256 %s)",
                     SKILL_NAME, batch, len(rows), lo, hi, rel, digest[:12])
            batch += 1

            if len(rows) < BATCH_SIZE:
                break

        log.info("%s: done -- %d row(s) archived", SKILL_NAME, archived)
    except Exception as e:
        log.error("%s: failed -- %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
