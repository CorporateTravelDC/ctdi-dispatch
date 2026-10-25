"""tests/scripts/test_persist_volumes.py -- 2026-10-24 (persistence audit, operator-approved):
every named volume or host state directory a tracked quadlet writes to is either in the
nightly Borg archive (scripts/backup/ctdi-backup.sh) or on the explicit, reasoned list of
things that are rebuildable / not state. Static checks only; nothing touches the live box.

Background: graphs1090's RRDs lived inside the ultrafeeder container and were wiped by every
image rebuild. The same image keeps /var/globe_history (traces, heatmap, readsb internal_state)
the same way, so that is a named volume too, and both ride in the archive like nextcloud-html.
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUADLETS = REPO / ".config/containers/systemd"
BACKUP_SH = REPO / "scripts/backup/ctdi-backup.sh"
BACKUP_MD = REPO / "docs/BACKUP.md"

# named volumes that are deliberately NOT archived raw, with the reason
VOLUMES_NOT_ARCHIVED = {
    "corporatetraveldc-pgsql-sock": "unix socket, runtime only",
    "corporatetraveldc-pgsql-demo-sock": "unix socket, runtime only",
    "corporatetraveldc-triggers": "runtime trigger files, empty between runs",
    "corporatetraveldc-pgsql-data": "dumped with pg_dump (dispatch Postgres), never read raw",
    "nextcloud-db-data": "dumped with pg_dump (Nextcloud Postgres), never read raw",
    "corporatetraveldc-pgsql-demo-data": "demo instance, rebuildable (BACKUP_EXCLUDED_INSTANCES)",
}
# read-write host bind mounts under /var/lib that are deliberately NOT archived
HOST_STATE_NOT_ARCHIVED = {
    "/var/lib/corporatetraveldc-demo": "demo-only sibling of the state dir, rebuildable",
}


def _active_lines(p: Path) -> list[str]:
    return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]


def _volume_lines():
    for p in sorted(QUADLETS.glob("*.container")):
        for l in _active_lines(p):
            if l.startswith("Volume="):
                spec = l[len("Volume="):]
                parts = spec.split(":")
                host, dest = parts[0], parts[1] if len(parts) > 1 else ""
                opts = parts[2] if len(parts) > 2 else ""
                yield p.name, host, dest, opts


def _backup_volumes() -> list[str]:
    m = re.search(r"^BACKUP_VOLUMES=\(([^)]*)\)", BACKUP_SH.read_text(), re.M)
    assert m, "BACKUP_VOLUMES=(...) missing from ctdi-backup.sh"
    return m.group(1).split()


def _backup_paths() -> str:
    m = re.search(r"^paths=\((.*)\)", BACKUP_SH.read_text(), re.M)
    assert m, "paths=(...) missing from ctdi-backup.sh"
    return m.group(1)


class UltrafeederPersists(unittest.TestCase):
    def test_both_history_dirs_are_named_volumes(self):
        mounts = {(h, d) for n, h, d, _ in _volume_lines() if n == "corporatetraveldc-ultrafeeder.container"}
        self.assertIn(("corporatetraveldc-ultrafeeder-graphs", "/var/lib/collectd"), mounts)
        self.assertIn(("corporatetraveldc-ultrafeeder-history", "/var/globe_history"), mounts)

    def test_history_volume_has_a_dated_reason(self):
        text = (QUADLETS / "corporatetraveldc-ultrafeeder.container").read_text()
        self.assertIn("# 2026-10-24", text)
        self.assertIn("corporatetraveldc-ultrafeeder-history:/var/globe_history", text)


class EveryVolumeIsAccountedFor(unittest.TestCase):
    def test_named_volumes_are_archived_or_listed_with_a_reason(self):
        archived = set(_backup_volumes())
        for unit, host, dest, opts in _volume_lines():
            if host.startswith("/") or host.startswith("%") or "ro" in opts.split(","):
                continue   # host path, template, or read-only
            with self.subTest(unit=unit, volume=host):
                self.assertTrue(host in archived or host in VOLUMES_NOT_ARCHIVED,
                                f"{unit}: named volume {host} ({dest}) is neither in BACKUP_VOLUMES nor on the reasoned skip list")

    def test_no_volume_is_both_archived_and_skipped(self):
        self.assertEqual(set(_backup_volumes()) & set(VOLUMES_NOT_ARCHIVED), set())

    def test_rw_host_state_dirs_are_archived_or_listed(self):
        paths = _backup_paths()
        for unit, host, dest, opts in _volume_lines():
            if not host.startswith("/var/lib/") or "ro" in opts.split(","):
                continue
            top = "/".join(host.split("/")[:4])   # /var/lib/<name>
            with self.subTest(unit=unit, host=host):
                self.assertTrue(top in paths or top in HOST_STATE_NOT_ARCHIVED,
                                f"{unit}: {host} is written by a container but {top} is not in the Borg path list")

    def test_archive_covers_the_2026_10_24_additions(self):
        vols = _backup_volumes()
        for v in ("nextcloud-html", "corporatetraveldc-ultrafeeder-graphs", "corporatetraveldc-ultrafeeder-history", "protonbridge-data"):
            self.assertIn(v, vols)
        paths = _backup_paths()
        for p in ("/var/lib/corporatetraveldc", "/etc/corporatetraveldc", "/var/lib/vnstat", "/var/lib/ntfy", "/var/lib/openwebui"):
            self.assertIn(p, paths)
        self.assertIn('"${vol_paths[@]}"', paths)


class BackupScriptShape(unittest.TestCase):
    def test_bash_syntax(self):
        subprocess.run(["bash", "-n", str(BACKUP_SH)], check=True)

    def test_missing_volume_is_skipped_not_fatal(self):
        text = BACKUP_SH.read_text()
        self.assertIn("podman volume inspect \"$v\" --format '{{.Mountpoint}}' 2>/dev/null", text)
        self.assertIn("not present -- skipped", text)
        self.assertNotIn("nc_vol=$(podman volume inspect nextcloud-html", text)   # replaced by the loop

    def test_volumes_are_read_under_podman_unshare(self):
        # the archive step runs borg inside podman unshare (volume files belong to the mapped uids)
        self.assertRegex(BACKUP_SH.read_text(), r"podman unshare env BORG_PASSCOMMAND=.*\\\n\s*borg create")

    def test_bridge_mail_cache_and_tar1090_checkout_are_excluded(self):
        text = BACKUP_SH.read_text()
        for pat in ("protonbridge-data/_data/.local/share/protonmail/bridge-v3/gluon",
                    "protonbridge-data/_data/.cache",
                    "/var/lib/openwebui/cache",
                    "corporatetraveldc-ultrafeeder-history/_data/tar1090-update"):
            self.assertIn(pat, text)


class DocsMatch(unittest.TestCase):
    def test_backup_doc_names_every_archived_volume_and_state_dir(self):
        doc = BACKUP_MD.read_text()
        for v in _backup_volumes():
            self.assertIn(f"`{v}`", doc)
        for p in ("/var/lib/ntfy", "/var/lib/openwebui"):
            self.assertIn(f"`{p}`", doc)
        self.assertIn("Offline / parallel off-site copy", doc)
        self.assertIn("podman cp corporatetraveldc-ultrafeeder:/var/globe_history/.", doc)

    def test_lockdown_readiness_table_covers_every_archived_volume(self):
        doc = BACKUP_MD.read_text()
        self.assertIn("## Lockdown readiness", doc)
        section = doc.split("## Lockdown readiness", 1)[1]
        for v in _backup_volumes():
            self.assertIn(f"`{v}`", section)
        for p in ("/var/lib/ntfy", "/var/lib/openwebui"):
            self.assertIn(f"`{p}`", section)
        # no NEW host bind path for state in this batch: the one new mount is a named volume
        for unit, host, dest, opts in _volume_lines():
            if dest == "/var/globe_history":
                self.assertFalse(host.startswith("/"), f"{unit}: {host} should be a named volume")


if __name__ == "__main__":
    unittest.main()


def test_operational_config_paths_are_archived_and_secrets_dir_excluded():
    # 2026-10-24: operator -- "just the operational directories in ~/ and /etc"
    src = (REPO / "scripts/backup/ctdi-backup.sh").read_text()
    for p in ("/etc/ntfy", "/etc/nftables.d", ".config/nextcloud", ".config/containers/systemd", "${op_paths[@]}"):
        assert p in src, p
    assert "system-connections" not in src.split("OP_PATHS=(")[1].split(")")[0]
