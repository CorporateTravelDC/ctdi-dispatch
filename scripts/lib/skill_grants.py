#!/usr/bin/env python3
"""scripts/lib/skill_grants.py -- per-agent / per-task skill grants with clawback.

Operator directive 2026-10-04: every skill we use should be available to any
agent, and be clawed back per agent or per task. Skills are INSTRUCTIONS, not
capabilities: clawback removes a skill from the agent's listing and makes any
use of it a recorded violation; the hard boundary stays on what a skill needs
(secrets subset, signer, routes). See docs/AGENT_SEGMENTATION.md "Skills".

Sources (the catalog):
  signed   skills/<name>/                 copied from the signed tree; every file
                                          checked against MANIFEST.sha256 first
  vendor   <operator>/.claude/skills/<n>  NOT redistributed in the repo (several
                                          are Proprietary-licensed); pinned by
                                          tree hash in skills/vendor-pins.txt
                                          (signed). A copy that does not match
                                          its pin is withheld.
  project  .claude/skills/<name>/         auto-loaded by any session whose cwd is
                                          the repo; clawback = skillOverrides
                                          "off" in the agent's settings.json

Grants file (/etc/ctdc-skill-grants.conf, root 0644, PARSED never sourced):
  grant|deny <account|*> <skill|*> [task=<id>] [until=<ISO-8601 with offset>]
A deny beats any grant. Expired lines are ignored (a timed deny lapses, a
timed grant lapses). A missing/unreadable grants file = HOLD: nothing changes.

Apply (root, hourly, --execute): for every agent account in the registry,
materialize the granted signed+vendor skills into ~/.claude/skills as
root-owned read-only copies (marker .ctdc-grant), remove revoked ones,
quarantine anything unsanctioned or tampered, and merge skillOverrides + the
context-guardian hooks into the agent's settings.json AS THE AGENT. All
filesystem work inside a home goes through O_NOFOLLOW directory fds.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pwd
import re
import stat
import sys
import time

GRANTS = "/etc/ctdc-skill-grants.conf"
REGISTRY = "/etc/ctdc-accounts.conf"
REPO = "/opt/corporatetraveldc/private/ctdi-dispatch-internal"
VENDOR_ROOT = "/home/corporatetraveldc/.claude/skills"
STATE_DIR = "/var/lib/ctdc-liveness"          # root-only, outside every container mount
MARKER = ".ctdc-grant"
QUARANTINE = ".ctdc-skills-quarantine"
SKIP_NAMES = {"__pycache__", ".DS_Store", MARKER}
SKILL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ACCT_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
GUARDIAN = ".claude/skills/dispatch-context-guardian/scripts"


# ---------------------------------------------------------------- grants file
class Rule:
    __slots__ = ("verb", "account", "skill", "task", "until", "lineno")

    def __init__(self, verb, account, skill, task, until, lineno):
        self.verb, self.account, self.skill = verb, account, skill
        self.task, self.until, self.lineno = task, until, lineno

    def active(self, now: dt.datetime) -> bool:
        return self.until is None or self.until > now

    def matches(self, account: str) -> bool:
        return self.account in ("*", account)

    def line(self) -> str:
        s = f"{self.verb} {self.account} {self.skill}"
        if self.task:
            s += f" task={self.task}"
        if self.until:
            s += f" until={self.until.isoformat()}"
        return s


def parse_until(v: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    if t.tzinfo is None:   # naive-date guard (optime): an offset is mandatory
        raise ValueError(f"until={v} has no UTC offset")
    return t


def parse_line(raw: str, lineno: int) -> Rule | None:
    line = raw.split("#", 1)[0].strip()
    if not line:
        return None
    parts = line.split()
    if len(parts) < 3 or parts[0] not in ("grant", "deny"):
        raise ValueError(f"line {lineno}: expected 'grant|deny <account|*> <skill|*> [task=] [until=]'")
    verb, account, skill = parts[:3]
    if account != "*" and not ACCT_RE.match(account):
        raise ValueError(f"line {lineno}: bad account {account!r}")
    if skill != "*" and not SKILL_RE.match(skill):
        raise ValueError(f"line {lineno}: bad skill {skill!r}")
    task = until = None
    for kv in parts[3:]:
        k, sep, v = kv.partition("=")
        if not sep or not v:
            raise ValueError(f"line {lineno}: expected key=value, got {kv!r}")
        if k == "task":
            if not SKILL_RE.match(v):
                raise ValueError(f"line {lineno}: bad task id {v!r}")
            task = v
        elif k == "until":
            try:
                until = parse_until(v)
            except ValueError as e:
                raise ValueError(f"line {lineno}: {e}") from None
        else:
            raise ValueError(f"line {lineno}: unknown key {k!r}")
    return Rule(verb, account, skill, task, until, lineno)


def load_rules(path: str) -> list[Rule]:
    """Raises OSError when unreadable (caller HOLDs), ValueError on bad syntax."""
    with open(path) as f:
        text = f.read()
    rules = []
    for i, raw in enumerate(text.splitlines(), 1):
        r = parse_line(raw, i)
        if r:
            rules.append(r)
    return rules


# -------------------------------------------------------------------- catalog
def tree_files(root: str):
    """(relpath, abspath) for every regular file, sorted; symlinks -> ValueError."""
    out = []
    for dp, dns, fns in os.walk(root, followlinks=False):
        dns[:] = sorted(d for d in dns if d not in SKIP_NAMES)
        for d in dns:
            if os.path.islink(os.path.join(dp, d)):
                raise ValueError(f"symlink in skill tree: {os.path.join(dp, d)}")
        for fn in sorted(fns):
            if fn in SKIP_NAMES or fn.endswith(".pyc"):
                continue
            p = os.path.join(dp, fn)
            if os.path.islink(p) or not os.path.isfile(p):
                raise ValueError(f"non-regular file in skill tree: {p}")
            out.append((os.path.relpath(p, root), p))
    return sorted(out)


def file_sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root: str) -> str:
    h = hashlib.sha256()
    for rel, p in tree_files(root):
        h.update(f"{rel}\0{file_sha(p)}\n".encode())
    return h.hexdigest()


def read_pins(repo: str) -> dict[str, str]:
    pins = {}
    p = os.path.join(repo, "skills", "vendor-pins.txt")
    if os.path.exists(p):
        for raw in open(p):
            line = raw.split("#", 1)[0].split()
            if len(line) == 2 and SKILL_RE.match(line[0]) and re.fullmatch(r"[0-9a-f]{64}", line[1]):
                pins[line[0]] = line[1]
    return pins


def read_needs(repo: str) -> dict[str, str]:
    needs = {}
    p = os.path.join(repo, "skills", "skill-capabilities.txt")
    if os.path.exists(p):
        for raw in open(p):
            line = raw.split("#", 1)[0].strip()
            if line:
                name, _, rest = line.partition(" ")
                needs[name] = rest.strip()
    return needs


def catalog(repo: str, vendor_root: str) -> dict[str, dict]:
    cat = {}
    sd = os.path.join(repo, "skills")
    for n in sorted(os.listdir(sd)) if os.path.isdir(sd) else []:
        if os.path.isdir(os.path.join(sd, n)) and SKILL_RE.match(n):
            cat[n] = {"source": "signed", "path": os.path.join(sd, n)}
    pd = os.path.join(repo, ".claude", "skills")
    for n in sorted(os.listdir(pd)) if os.path.isdir(pd) else []:
        if os.path.isdir(os.path.join(pd, n)) and n not in cat and SKILL_RE.match(n):
            cat[n] = {"source": "project", "path": os.path.join(pd, n)}
    for n, pin in read_pins(repo).items():
        if n not in cat:
            cat[n] = {"source": "vendor", "path": os.path.join(vendor_root, n), "pin": pin}
    return cat


# ----------------------------------------------------------------- resolution
def resolve(rules, account, cat, now=None):
    """-> ({skill: grant-info}, denied set, warnings)"""
    now = now or dt.datetime.now(dt.timezone.utc)
    granted, denied, warn = {}, set(), []
    for r in rules:
        if r.skill != "*" and r.skill not in cat:
            warn.append(f"line {r.lineno}: unknown skill {r.skill!r} (not in the catalog) -- ignored")
            continue
        if not (r.matches(account) and r.active(now)):
            continue
        names = list(cat) if r.skill == "*" else [r.skill]
        for n in names:
            if r.verb == "deny":
                denied.add(n)
            else:
                cur = granted.get(n)
                info = {"task": r.task, "until": r.until.isoformat() if r.until else None, "line": r.lineno}
                # a permanent grant outranks a timed one; else keep the latest expiry
                if cur is None or (cur["until"] and (info["until"] is None or info["until"] > cur["until"])):
                    granted[n] = info
    eff = {n: v for n, v in granted.items() if n not in denied}
    return eff, denied & set(cat), warn


def agent_accounts(registry: str) -> list[tuple[str, str]]:
    """(name, login_mode) for kind=agent rows of /etc/ctdc-accounts.conf.
    Before the registry exists (the first agent predates it), fall back to the
    ctdc-agents group with login mode claude -- the same rule as
    render-onboarding.sh. Tests (FAKE) never fall back."""
    if not os.path.exists(registry) and not os.environ.get("SKILL_GRANTS_FAKE_ROOT"):
        import grp
        try:
            members = grp.getgrnam("ctdc-agents").gr_mem
        except KeyError:
            raise FileNotFoundError(2, "No such file or directory (and no ctdc-agents group)") from None
        return [(m, "claude") for m in sorted(members) if ACCT_RE.match(m)]
    out = []
    for raw in open(registry):
        p = raw.split("#", 1)[0].split()
        if len(p) >= 3 and p[1] == "agent" and ACCT_RE.match(p[0]):
            out.append((p[0], p[2]))
    return out


# ----------------------------------------------------- fd-relative file ops
O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def open_dir(name, dir_fd):
    return os.open(name, O_DIR, dir_fd=dir_fd)


def listdir_fresh(dir_fd):
    """os.listdir on a NEW open file description of the directory. btrfs
    (kernel >= 6.5) fixes a directory's readdir range when it is opened, and
    listing through an already-open fd (os.listdir dups, sharing that range)
    misses entries created after the open (2026-10-04 first real apply: every
    copy hashed as empty). tmpfs does not do this, which hid it in tests."""
    fresh = os.open(".", O_DIR, dir_fd=dir_fd)
    try:
        return os.listdir(fresh)
    finally:
        os.close(fresh)


def lexists(name, dir_fd):
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def rmtree_fd(name, dir_fd):
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=dir_fd)
        return
    fd = open_dir(name, dir_fd)
    try:
        for e in listdir_fresh(fd):
            rmtree_fd(e, fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=dir_fd)


def fd_tree_hash(dir_fd, rel=""):
    """tree_hash over an fd-opened directory (never follows links)."""
    items = []
    for e in sorted(listdir_fresh(dir_fd)):
        if e in SKIP_NAMES or e.endswith(".pyc"):
            continue
        st = os.stat(e, dir_fd=dir_fd, follow_symlinks=False)
        r = f"{rel}{e}"
        if stat.S_ISDIR(st.st_mode):
            sub = open_dir(e, dir_fd)
            try:
                items.extend(fd_tree_hash(sub, r + "/"))
            finally:
                os.close(sub)
        elif stat.S_ISREG(st.st_mode):
            h = hashlib.sha256()
            fd = os.open(e, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
            with os.fdopen(fd, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
            items.append((r, h.hexdigest()))
        else:
            items.append((r, "NOT-A-REGULAR-FILE"))
    return items


def digest(items):
    h = hashlib.sha256()
    for r, s in sorted(items):
        h.update(f"{r}\0{s}\n".encode())
    return h.hexdigest()


# ---------------------------------------------------------------------- apply
class Applier:
    def __init__(self, a):
        self.a, self.findings, self.notes = a, [], []
        self.fake = bool(os.environ.get("SKILL_GRANTS_FAKE_ROOT"))
        self.mutate = a.execute and (self.fake or os.geteuid() == 0)
        self.root_uid = int(os.environ.get("SKILL_GRANTS_FAKE_ROOT_UID", os.getuid())) if self.fake else 0
        self.manifest = self._manifest()

    def _manifest(self):
        m = {}
        p = os.path.join(self.a.repo, "MANIFEST.sha256")
        if os.path.exists(p):
            for raw in open(p):
                parts = raw.split()
                if len(parts) == 2:
                    m[parts[1].lstrip("*")] = parts[0]
        return m

    def find(self, msg):
        self.findings.append(msg)
        print(f"[FINDING] {msg}")

    def note(self, msg):
        self.notes.append(msg)
        print(f"[info] {msg}")

    def home_of(self, acct):
        if self.a.home_base:
            return os.path.join(self.a.home_base, acct)
        return pwd.getpwnam(acct).pw_dir

    def owner(self, acct):
        if self.fake:
            return os.getuid(), os.getgid()
        pw = pwd.getpwnam(acct)
        return pw.pw_uid, pw.pw_gid

    # source verification -> list of (rel, abspath, mode) or None (withheld)
    def source_files(self, name, entry):
        try:
            files = tree_files(entry["path"])
        except (ValueError, OSError) as e:
            self.find(f"{name}: source unusable ({e}) -- withheld from every agent")
            return None
        if entry["source"] == "signed":
            for rel, p in files:
                key = f"skills/{name}/{rel}"
                want = self.manifest.get(key)
                if want is None or file_sha(p) != want:
                    self.find(f"{name}: {key} {'not in' if want is None else 'differs from'} the signed manifest -- withheld")
                    return None
        elif entry["source"] == "vendor":
            have = digest([(rel, file_sha(p)) for rel, p in files])
            if have != entry["pin"]:
                self.find(f"{name}: vendor copy {have[:12]} does not match signed pin {entry['pin'][:12]} -- withheld (re-pin + sign if the upgrade is intended)")
                return None
        return [(rel, p, os.stat(p).st_mode) for rel, p in files], digest([(rel, file_sha(p)) for rel, p in files])

    def install_copy(self, name, files, want, root_fd, uid, gid, info):
        tmp = f".{name}.new"
        if lexists(tmp, root_fd):
            rmtree_fd(tmp, root_fd)
        os.mkdir(tmp, 0o755, dir_fd=root_fd)
        tfd = open_dir(tmp, root_fd)
        try:
            for rel, src, mode in files:
                parts = rel.split("/")
                cur = os.dup(tfd)
                try:
                    for d in parts[:-1]:
                        if not lexists(d, cur):
                            os.mkdir(d, 0o755, dir_fd=cur)
                            if not self.fake:
                                os.chown(d, 0, gid, dir_fd=cur, follow_symlinks=False)
                        nxt = open_dir(d, cur)
                        os.close(cur)
                        cur = nxt
                    fmode = 0o755 if mode & 0o111 else 0o644
                    fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, fmode, dir_fd=cur)
                    with open(src, "rb") as s, os.fdopen(fd, "wb") as d:
                        d.write(s.read())
                    if not self.fake:
                        os.chown(parts[-1], 0, gid, dir_fd=cur, follow_symlinks=False)
                    os.chmod(parts[-1], fmode, dir_fd=cur, follow_symlinks=False)
                finally:
                    os.close(cur)
            got = digest(fd_tree_hash(tfd))
            if got != want:
                raise OSError(f"copy of {name} hashed {got[:12]}, expected {want[:12]}")
            fd = os.open(MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=tfd)
            with os.fdopen(fd, "w") as m:
                json.dump({"skill": name, "hash": want, **info}, m, sort_keys=True)
            if not self.fake:
                os.chown(MARKER, 0, gid, dir_fd=tfd, follow_symlinks=False)
                os.chown(tmp, 0, gid, dir_fd=root_fd, follow_symlinks=False)
        finally:
            os.close(tfd)
        if lexists(name, root_fd):
            rmtree_fd(name, root_fd)
        os.rename(tmp, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)

    def read_marker(self, name, root_fd):
        try:
            sub = open_dir(name, root_fd)
        except OSError:
            return None, None
        try:
            try:
                fd = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sub)
                with os.fdopen(fd) as f:
                    marker = json.load(f)
            except (OSError, ValueError):
                marker = None
            return marker, digest(fd_tree_hash(sub))
        finally:
            os.close(sub)

    def quarantine(self, name, claude_fd, root_fd, why):
        if not lexists(QUARANTINE, claude_fd):
            os.mkdir(QUARANTINE, 0o700, dir_fd=claude_fd)
            if not self.fake:
                os.chown(QUARANTINE, 0, 0, dir_fd=claude_fd, follow_symlinks=False)
        qfd = open_dir(QUARANTINE, claude_fd)
        try:
            dest = f"{time.strftime('%Y%m%dT%H%M%S')}-{name}"
            os.rename(name, dest, src_dir_fd=root_fd if root_fd is not None else claude_fd, dst_dir_fd=qfd)
        finally:
            os.close(qfd)
        return f"~/.claude/{QUARANTINE}/{dest} ({why})"

    def apply_account(self, acct, mode, rules, cat):
        eff, denied, _ = resolve(rules, acct, cat)
        home = self.home_of(acct)
        uid, gid = self.owner(acct)
        try:
            hfd = os.open(home, O_DIR)
        except OSError as e:
            self.find(f"{acct}: home {home} unusable ({e}) -- skipped")
            return
        try:
            try:
                cfd = open_dir(".claude", hfd)
            except FileNotFoundError:
                self.note(f"{acct}: no ~/.claude yet (account not provisioned) -- skipped")
                return
            except OSError as e:
                self.find(f"{acct}: ~/.claude is not a plain directory ({e}) -- skipped")
                return
            try:
                self._apply_skills(acct, eff, cat, cfd, gid)
                if mode == "claude":
                    self._apply_settings(acct, denied, cat, cfd, uid, gid)
            finally:
                os.close(cfd)
        finally:
            os.close(hfd)

    def _apply_skills(self, acct, eff, cat, cfd, gid):
        want = {n: v for n, v in eff.items() if cat[n]["source"] in ("signed", "vendor")}
        # the skills root itself must be a real directory owned by root
        if lexists("skills", cfd):
            st = os.stat("skills", dir_fd=cfd, follow_symlinks=False)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != self.root_uid:
                why = "symlink/non-directory" if not stat.S_ISDIR(st.st_mode) else "not root-owned (pre-grants layout or tamper)"
                if self.mutate:
                    self.find(f"{acct}: ~/.claude/skills {why} -> quarantined to {self.quarantine('skills', cfd, None, why)}")
                else:
                    self.find(f"{acct}: ~/.claude/skills {why} (would quarantine)")
                    return
        if not lexists("skills", cfd):
            if not self.mutate:
                self.note(f"{acct}: would create root-owned ~/.claude/skills with {len(want)} skill(s)")
                return
            os.mkdir("skills", 0o755, dir_fd=cfd)
            if not self.fake:
                os.chown("skills", 0, gid, dir_fd=cfd, follow_symlinks=False)
        rfd = open_dir("skills", cfd)
        try:
            for e in sorted(listdir_fresh(rfd)):
                if e in want:
                    continue
                marker, _ = self.read_marker(e, rfd)
                if marker and marker.get("skill") == e:
                    if self.mutate:
                        rmtree_fd(e, rfd)
                    self.note(f"{acct}: {e} revoked{'' if self.mutate else ' (would remove)'}")
                elif self.mutate:
                    self.find(f"{acct}: unsanctioned skill {e!r} -> {self.quarantine(e, cfd, rfd, 'no grant')}")
                else:
                    self.find(f"{acct}: unsanctioned skill {e!r} (would quarantine)")
            for n, info in sorted(want.items()):
                src = self.source_files(n, cat[n])
                if src is None:
                    continue
                files, h = src
                marker, have = self.read_marker(n, rfd) if lexists(n, rfd) else (None, None)
                minfo = {"source": cat[n]["source"], "task": info["task"], "until": info["until"]}
                if marker and have == h and marker.get("hash") == h and all(marker.get(k) == v for k, v in minfo.items()):
                    continue
                if marker is not None and have != marker.get("hash"):
                    self.find(f"{acct}: {n} was modified in place -- restored from the {cat[n]['source']} copy")
                elif marker is None and lexists(n, rfd):
                    self.find(f"{acct}: {n} has no grant marker -- replaced")
                if self.mutate:
                    try:
                        self.install_copy(n, files, h, rfd, None, gid, minfo)
                    except OSError as e:   # one bad skill never aborts the run
                        self.find(f"{acct}: {n} not installed ({e})")
                        if lexists(f".{n}.new", rfd):
                            rmtree_fd(f".{n}.new", rfd)
                        continue
                    self.note(f"{acct}: installed {n} ({cat[n]['source']}{', task ' + info['task'] if info['task'] else ''}{', until ' + info['until'] if info['until'] else ''})")
                else:
                    self.note(f"{acct}: would install {n}")
        finally:
            os.close(rfd)

    def _apply_settings(self, acct, denied, cat, cfd, uid, gid):
        project_denied = sorted(n for n in denied if cat[n]["source"] == "project")
        hooks = self.guardian_hooks()
        if not self.mutate:
            self.note(f"{acct}: would set skillOverrides off for {project_denied or 'nothing'} + guardian hooks")
            return
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:   # child: drop to the agent BEFORE touching its settings file
            os.close(r)
            code = 0
            try:
                if not self.fake:
                    os.setgroups([])
                    os.setgid(gid)
                    os.setuid(uid)
                msgs = write_settings(cfd, project_denied, hooks)
                os.write(w, json.dumps(msgs).encode())
            except Exception as e:  # noqa: BLE001 -- reported by the parent
                os.write(w, json.dumps([f"ERROR {e}"]).encode())
                code = 1
            os.close(w)
            os._exit(code)
        os.close(w)
        buf = b""
        while chunk := os.read(r, 65536):
            buf += chunk
        os.close(r)
        os.waitpid(pid, 0)
        for m in json.loads(buf or b"[]"):
            (self.find if m.startswith(("ERROR", "TAMPER")) else self.note)(f"{acct}: settings: {m}")

    def guardian_hooks(self):
        base = os.path.join(self.a.checkout, GUARDIAN)
        return {
            "Stop": (None, f"python3 {base}/context_hook.py"),
            "SessionStart": ("compact|resume", f"python3 {base}/restore_dispatch_state.py 2>/dev/null || true"),
        }


def merge_settings(data: dict, project_denied: list[str], hooks: dict) -> tuple[dict, list[str]]:
    """Pure merge: managed skillOverrides + guardian hooks; every other key kept."""
    msgs = []
    ov = data.get("skillOverrides")
    ov = dict(ov) if isinstance(ov, dict) else {}
    prev = set(data.get("ctdcManagedSkillOverrides") or [])
    for n in sorted(prev - set(project_denied)):
        if ov.get(n) == "off":
            del ov[n]
            msgs.append(f"{n} re-granted (override removed)")
    for n in project_denied:
        if n in prev and ov.get(n) != "off":
            msgs.append(f"TAMPER {n} override was changed by the account -- reset to off")
        elif n not in prev:
            msgs.append(f"{n} clawed back (skillOverrides off)")
        ov[n] = "off"
    if ov:
        data["skillOverrides"] = ov
    else:
        data.pop("skillOverrides", None)
    data["ctdcManagedSkillOverrides"] = list(project_denied)
    hk = data.setdefault("hooks", {})
    for event, (matcher, cmd) in hooks.items():
        entries = hk.setdefault(event, [])
        present = any(h.get("command") == cmd for e in entries for h in e.get("hooks", []))
        if not present:
            e = {"hooks": [{"type": "command", "command": cmd, **({"timeout": 90} if event == "SessionStart" else {})}]}
            if matcher:
                e = {"matcher": matcher, **e}
            entries.append(e)
            msgs.append(f"{event} guardian hook added")
    return data, msgs


def write_settings(cfd: int, project_denied, hooks) -> list[str]:
    try:
        fd = os.open("settings.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=cfd)
        with os.fdopen(fd) as f:
            raw = f.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return ["ERROR settings.json is not a JSON object -- left untouched"]
    except FileNotFoundError:
        data = {}
    except ValueError:
        return ["ERROR settings.json is not valid JSON -- left untouched"]
    new, msgs = merge_settings(data, project_denied, hooks)
    if not msgs:
        return []
    tmp = ".settings.json.ctdc-new"
    try:
        os.unlink(tmp, dir_fd=cfd)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=cfd)
    with os.fdopen(fd, "w") as f:
        json.dump(new, f, indent=2)
        f.write("\n")
    os.rename(tmp, "settings.json", src_dir_fd=cfd, dst_dir_fd=cfd)
    return msgs


def cmd_apply(a) -> int:
    if not a.execute:
        print("[info] dry run -- nothing changes (pass --execute as root to apply)")
    try:
        rules = load_rules(a.grants)
    except OSError as e:
        print(f"[HOLD] grants file {a.grants} unreadable ({e.strerror}) -- nothing changed")
        return 0
    except ValueError as e:
        print(f"[HOLD] grants file has a syntax error ({e}) -- nothing changed")
        return 1
    try:
        accounts = agent_accounts(a.registry)
    except OSError as e:
        print(f"[HOLD] registry {a.registry} unreadable ({e.strerror}) -- nothing changed")
        return 0
    cat = catalog(a.repo, a.vendor_root)
    ap = Applier(a)
    for w in resolve(rules, "", cat)[2]:
        ap.find(w)
    for acct, mode in accounts:
        ap.apply_account(acct, mode, rules, cat)
    if ap.mutate and a.state_dir:
        os.makedirs(a.state_dir, mode=0o700, exist_ok=True)
        rep = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "findings": ap.findings, "notes": ap.notes}
        with open(os.path.join(a.state_dir, "skill-grants.json"), "w") as f:
            json.dump(rep, f, indent=2)
    print(f"[summary] {len(accounts)} agent account(s), {len(ap.findings)} finding(s)")
    return 1 if ap.findings else 0


# --------------------------------------------------------- list / render / edit
def cmd_list(a) -> int:
    rules = load_rules(a.grants)
    cat = catalog(a.repo, a.vendor_root)
    accts = [a.account] if a.account else [n for n, _ in agent_accounts(a.registry)]
    for acct in accts:
        eff, denied, warn = resolve(rules, acct, cat)
        print(f"{acct}:")
        for n in sorted(cat):
            if n in eff:
                i = eff[n]
                extra = "".join(f" {k}={i[k]}" for k in ("task", "until") if i[k])
                print(f"  + {n:32s} {cat[n]['source']}{extra}")
            elif n in denied:
                print(f"  - {n:32s} {cat[n]['source']} (clawed back)")
        for w in warn:
            print(f"  ! {w}")
    return 0


def cmd_render(a) -> int:
    cat = catalog(a.repo, a.vendor_root)
    needs = read_needs(a.repo)
    try:
        rules = load_rules(a.grants)
    except (OSError, ValueError) as e:
        print(f"_(grants file unavailable: {e} -- ask the operator which skills you hold)_")
        return 0
    eff, denied, _ = resolve(rules, a.account, cat)
    if not eff:
        print("_(none granted -- request one with a signed board post; see below)_")
    else:
        print("| skill | from | scope | needs |")
        print("|---|---|---|---|")
        for n in sorted(eff):
            i = eff[n]
            scope = "standing"
            if i["task"] or i["until"]:
                scope = " ".join(x for x in (f"task `{i['task']}`" if i["task"] else "", f"until {i['until']}" if i["until"] else "") if x)
            print(f"| `{n}` | {cat[n]['source']} | {scope} | {needs.get(n, '-')} |")
    if denied:
        print(f"\nClawed back from you right now: {', '.join(f'`{n}`' for n in sorted(denied))}.")
    return 0


def cmd_edit(a) -> int:
    if os.geteuid() != 0 and not os.environ.get("SKILL_GRANTS_FAKE_ROOT"):
        print("edit: run with sudo (the grants file is root-owned)", file=sys.stderr)
        return 77
    try:
        text = open(a.grants).read()
    except FileNotFoundError:
        text = "# /etc/ctdc-skill-grants.conf -- see scripts/lib/skill_grants.py (parsed, never sourced)\n"
    cat = catalog(a.repo, a.vendor_root)
    if a.skill != "*" and a.skill not in cat:
        print(f"edit: unknown skill {a.skill!r}; known: {', '.join(sorted(cat))}", file=sys.stderr)
        return 65
    extra = []
    if a.task:
        extra.append(f"task={a.task}")
    if a.until:
        u = parse_until(a.until)
        if u <= dt.datetime.now(dt.timezone.utc):
            print("edit: --until is in the past", file=sys.stderr)
            return 65
        extra.append(f"until={u.isoformat()}")
    keep = []
    for i, raw in enumerate(text.splitlines(), 1):
        r = parse_line(raw, i)
        same = r and r.account == a.account and r.skill == a.skill
        if same and (a.verb == "revoke" or (a.verb == "grant" and r.verb == "deny")):
            continue
        keep.append(raw)
    if a.verb != "revoke":
        line = " ".join([a.verb, a.account, a.skill, *extra])
        if line not in keep:
            keep.append(line)
    new = "\n".join(keep) + "\n"
    for i, raw in enumerate(new.splitlines(), 1):
        parse_line(raw, i)   # validate before writing
    tmp = a.grants + ".new"
    with open(tmp, "w") as f:
        f.write(new)
    os.chmod(tmp, 0o644)
    os.replace(tmp, a.grants)
    print(f"{a.verb} {a.account} {a.skill} {' '.join(extra)} -> {a.grants}; takes effect at the next apply (or run: skill-grants.sh apply --execute)")
    return 0


def cmd_pin_vendor(a) -> int:
    p = os.path.join(a.repo, "skills", "vendor-pins.txt")
    pins = read_pins(a.repo)
    for n in a.names:
        d = os.path.join(a.vendor_root, n)
        pins[n] = tree_hash(d)
        print(f"{n} {pins[n]}")
    with open(p, "w") as f:
        f.write("# skills/vendor-pins.txt -- vendor skills grantable to agents, pinned by tree hash.\n"
                "# The bytes are NOT in this repo (several are Proprietary-licensed); root copies the\n"
                "# operator's installed copy only when it matches this signed pin. Regenerate with\n"
                "# scripts/skill-grants.sh pin-vendor <name>... then sign.\n")
        for n in sorted(pins):
            f.write(f"{n} {pins[n]}\n")
    return 0


def main(argv=None) -> int:
    fake = os.environ.get("SKILL_GRANTS_FAKE_ROOT")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--grants", default=os.path.join(fake, "grants.conf") if fake else GRANTS)
    ap.add_argument("--registry", default=os.path.join(fake, "accounts.conf") if fake else REGISTRY)
    ap.add_argument("--repo", default=os.environ.get("CTDC_REPO_ROOT", REPO))
    ap.add_argument("--checkout", default=REPO, help="repo path the agents' hooks point at")
    ap.add_argument("--vendor-root", default=os.path.join(fake, "vendor") if fake else VENDOR_ROOT)
    ap.add_argument("--home-base", default=os.path.join(fake, "home") if fake else None)
    ap.add_argument("--state-dir", default=os.path.join(fake, "state") if fake else STATE_DIR)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("apply"); p.add_argument("--execute", action="store_true")
    p = sub.add_parser("list"); p.add_argument("account", nargs="?")
    p = sub.add_parser("render"); p.add_argument("account")
    p = sub.add_parser("check")
    for verb in ("grant", "deny", "revoke"):
        p = sub.add_parser(verb); p.add_argument("account"); p.add_argument("skill")
        p.add_argument("--task"); p.add_argument("--until")
    p = sub.add_parser("pin-vendor"); p.add_argument("names", nargs="+")
    p = sub.add_parser("tree-hash"); p.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "apply":
        return cmd_apply(a)
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "render":
        return cmd_render(a)
    if a.cmd == "check":
        try:
            rules = load_rules(a.grants)
        except (OSError, ValueError) as e:
            print(f"grants: {e}")
            return 1
        warn = resolve(rules, "", catalog(a.repo, a.vendor_root))[2]
        for w in warn:
            print(w)
        print(f"{len(rules)} rule(s), {len(warn)} warning(s)")
        return 1 if warn else 0
    if a.cmd in ("grant", "deny", "revoke"):
        a.verb = a.cmd
        return cmd_edit(a)
    if a.cmd == "pin-vendor":
        return cmd_pin_vendor(a)
    if a.cmd == "tree-hash":
        print(tree_hash(a.dir))
        return 0
    return 64


if __name__ == "__main__":
    sys.exit(main())
