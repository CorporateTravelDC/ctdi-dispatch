#!/usr/bin/env python3
"""
scripts/doc-claims-check.py -- prose docs vs the live system, deterministically.

2026-10-16 (operator): the weekly doc drift check had stopped checking PROSE
docs against the box; it only ran the CLAUDE.md checker and listed units and
containers. This extracts, from README.md, docs/*.md (non-dated: no
YYYY-MM-DD in the filename) and skills/*/SKILL.md, every

  * repo path claim        -- `scripts/...`, `src/...`, `.config/...`, `docs/...`
  * unit claim             -- `corporatetraveldc-*.timer` / `.service`
  * schedule claim         -- `OnCalendar=...` / `OnUnitActiveSec=...` on the
                              same line as a `corporatetraveldc-*.timer` name

and verifies each against `git ls-files`, `systemctl --user list-unit-files`
(+ the system manager's), and the installed timer's own OnCalendar /
OnUnitActiveSec (`systemctl --user cat`, then `systemctl cat`). Struck text
(`~~...~~`, `<del>...</del>`) is superseded by definition and is skipped, as is a line carrying
`<!-- not-a-claim -->` (a deliberate mention of something absent or external), and
docs/RETIRED.md (it exists to name what is no longer live). A unit tracked
under scripts/agent-segmentation/units/ is installed in an agent account's
own user manager, which this account's `systemctl --user` cannot see, so it
is not reported; a SKILL.md path is also tried relative to its skill dir.

2026-11-02: the scope widened (see collect_docs), `--public-ref REF` also checks the public mirror
(dead links to scrub-dropped files; docs changed on HEAD and not mirrored within --max-age-hours),
and `--age-ledger FILE` keeps first-seen times so a miss open past --max-age-hours (24) prints
`[AGED] ...` and the summary gains `aged=A`. The daily timer passes both.

Output: one `[DRIFT] <doc>:<line>: ...` line per miss, then
`[SUMMARY] docs=N claims=M misses=K`. Exit 1 when K > 0, else 0. Stdlib only.

Usage:
  doc-claims-check.py                      # this repo, live facts from the box
  doc-claims-check.py --claims-only DIR    # README.md, docs/, skills/ under DIR
  doc-claims-check.py --live-facts FILE    # JSON {"tracked": [...], "units": [...],
                                           #       "timers": {"x.timer": {"OnCalendar": [...],
                                           #                              "OnUnitActiveSec": [...]}}}
                                           # instead of git/systemctl (tests)
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
import time
from pathlib import Path

DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
STRIKE_INLINE = re.compile(r"~~.+?~~")
STRIKE_DEL = re.compile(r"<del>.*?</del>", re.S)

PATH_CLAIM = re.compile(r"(?<![\w/.~-])((?:scripts|src|\.config|docs)/[A-Za-z0-9_./@+-]*)")
UNIT_CLAIM = re.compile(r"(?<![\w/.-])(corporatetraveldc-[A-Za-z0-9_@%.-]*?\.(?:timer|service))(?![\w-])")
SCHEDULE_CLAIM = re.compile(r"(OnCalendar|OnUnitActiveSec)=([^`|;)\n]+)")
# 2026-10-26 (operator: catch the drift the docs sweep had to fix by hand): API routes a doc names
# must exist in the route code; "unit ... enabled|disabled" must match systemctl is-enabled.
ROUTE_CLAIM = re.compile(r"(?<![\w.:/-])(/api/[A-Za-z0-9_/{}<>.:-]+)")
ROUTE_SRC = re.compile(r"""["'](/api/[A-Za-z0-9_/{}<>.:-]+)["']""")
STATE_CLAIM = re.compile(r"\b(enabled|disabled)\b", re.I)
ROUTER_PREFIX = re.compile(r"""APIRouter\([^)]*prefix\s*=\s*["'](/api/[^"']*)["']""")
DECORATED_PATH = re.compile(r"""@\w+\.(?:get|post|put|patch|delete|api_route)\(\s*["']([^"']*)["']""")


def route_key(r: str) -> str:
    """Normalise a route for comparison: parameters ({x}, <x>, :x) become *, no trailing slash."""
    r = r.rstrip(".,;:)`'\"").rstrip("/")
    return "/".join("*" if re.fullmatch(r"\{[^}]*\}|<[^>]*>|:[A-Za-z_]\w*", s) else s for s in r.split("/"))
PATH_TRAIL = ".,;:)'\"]}>"
SKIP_DOCS = {"docs/RETIRED.md"}
ROOT_DOCS = ("README.md", "SECURITY.md")
FROZEN_DOC_DIRS = ("docs/investor-materials/", "docs/security-reviews/")
EXTRA_DOCS = ("scripts/agent-segmentation/onboarding-template.md",)   # every account's pamphlet source
AGENT_UNITS_DIR = "scripts/agent-segmentation/units/"


def _blank_keep_lines(m: re.Match) -> str:
    return "\n" * m.group(0).count("\n")


# 2026-11-02: a line that deliberately names something absent (a retired script, a doc noted as
# missing, an external service's URL path) carries this HTML comment and is not a claim.
NOT_A_CLAIM = "<!-- not-a-claim -->"


def strip_struck(text: str) -> str:
    text = STRIKE_DEL.sub(_blank_keep_lines, text)
    return "\n".join("" if NOT_A_CLAIM in ln else STRIKE_INLINE.sub("", ln) for ln in text.split("\n"))


def _undated(p: Path, root: Path) -> bool:
    """No dated path component: a dated file or a dated folder is a record of its day, not a claim."""
    return not any(DATED.search(part) for part in p.relative_to(root).parts)


def collect_docs(root: Path) -> list[Path]:
    """2026-11-02 (pre-pentest docs parity): the sweep read README.md, docs/*.md and skills/*/SKILL.md
    only, so SECURITY.md, every docs/ subfolder, the .agents skill twins, the pamphlet template and the
    src/ READMEs were never checked. Undated docs anywhere under docs/ now count; FROZEN_DOC_DIRS are
    dated records or frozen releases kept for the record."""
    docs: list[Path] = []
    for name in ROOT_DOCS:
        if (root / name).is_file():
            docs.append(root / name)
    docs += sorted(p for p in root.glob("CONTRIBUTING*.md") if p.is_file())
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        docs += sorted(p for p in docs_dir.rglob("*.md")
                       if p.is_file() and _undated(p, root)
                       and p.relative_to(root).as_posix() not in SKIP_DOCS
                       and not p.relative_to(root).as_posix().startswith(FROZEN_DOC_DIRS))
    for skills_dir in ("skills", ".agents/skills"):
        if (root / skills_dir).is_dir():
            docs += sorted((root / skills_dir).glob("*/SKILL.md"))
    for extra in EXTRA_DOCS:
        if (root / extra).is_file():
            docs.append(root / extra)
    if (root / "src").is_dir():
        docs += sorted(p for p in (root / "src").rglob("README.md") if p.is_file())
    # A git checkout carries untracked and ignored trees (node_modules READMEs, local reports): only
    # tracked docs are claims. Outside git (synthetic test trees) every collected file counts.
    tracked = set(_run(["git", "ls-files"], cwd=root).split("\n")) - {""}
    if tracked:
        docs = [d for d in docs if d.relative_to(root).as_posix() in tracked]
    return docs


def extract_claims(doc: Path, root: Path) -> list[tuple[str, int, str, str]]:
    """[(kind, line_no, value, extra)] -- kind in path|unit|schedule."""
    rel = doc.relative_to(root).as_posix()
    claims: list[tuple[str, int, str, str]] = []
    text = strip_struck(doc.read_text(encoding="utf-8", errors="replace"))
    # Fenced code is checked too: a command in a doc names real paths/units.
    for no, line in enumerate(text.split("\n"), 1):
        for m in PATH_CLAIM.finditer(line):
            end = m.end()
            if end < len(line) and line[end] in "*<{$":
                continue                                   # glob / placeholder
            p = m.group(1).rstrip(PATH_TRAIL)
            if any(ch in p for ch in "*<>{}$") or p.endswith("/."):
                continue
            claims.append(("path", no, p, rel))
        units_here = []
        for m in UNIT_CLAIM.finditer(line):
            u = m.group(1)
            if "%" in u and "@" not in u:
                continue
            claims.append(("unit", no, u, rel))
            if u.endswith(".timer"):
                units_here.append(u)
        for m in ROUTE_CLAIM.finditer(line):
            claims.append(("route", no, m.group(1), rel))
        all_units = [m.group(1) for m in UNIT_CLAIM.finditer(line)]
        st = STATE_CLAIM.findall(line)
        if len(all_units) == 1 and len({s.lower() for s in st}) == 1 and "@" not in all_units[0]:
            claims.append(("state", no, st[0].lower(), f"{rel}\t{all_units[0]}"))
        if len(units_here) == 1:
            for m in SCHEDULE_CLAIM.finditer(line):
                expr = " ".join(m.group(2).split()).rstrip(PATH_TRAIL + "*").strip()
                if not expr or expr.startswith("...") or "<" in expr:
                    continue
                claims.append(("schedule", no, f"{m.group(1)}={expr}", f"{rel}\t{units_here[0]}"))
    return claims


# ── live facts ──────────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class LiveFacts:
    def __init__(self, root: Path, facts_file: Path | None):
        self.root = root
        self._timer_cache: dict[str, dict[str, list[str]]] = {}
        self.partial = False
        if facts_file and facts_file.name == "status.json":
            # 2026-10-26 (operator): an agent account (EA) runs the same check without seeing the
            # operator's user manager -- units come from the published status snapshot; enabled
            # state and timer schedules are not in the snapshot, so those claims are skipped.
            snap = json.loads(facts_file.read_text())
            self.tracked = set(_run(["git", "ls-files"], cwd=root).split("\n")) - {""}
            self.units = {u["unit"] for u in snap.get("units", [])} | {t["unit"] for t in snap.get("timers", [])}
            self.routes = set()
            facts_file = None
            self.synthetic = True
            self.partial = True
            self.states = {}
            self._load_routes(root)
            return
        if facts_file:
            data = json.loads(facts_file.read_text())
            self.tracked = set(data.get("tracked", []))
            self.units = set(data.get("units", []))
            self._timer_cache = {k: {kk: list(vv) for kk, vv in v.items()}
                                 for k, v in data.get("timers", {}).items()}
            self.routes = {route_key(r) for r in data.get("routes", [])}
            self.states = dict(data.get("states", {}))
            self.synthetic = True
        else:
            self.routes = set()
            self._load_routes(root)
            self.states = {}
            self.tracked = set(_run(["git", "ls-files"], cwd=root).split("\n")) - {""}
            units: set[str] = set()
            for scope in (["--user"], []):
                out = _run(["systemctl", *scope, "list-unit-files", "corporatetraveldc-*",
                            "--no-legend", "--plain"])
                units |= {ln.split()[0] for ln in out.splitlines() if ln.strip()}
            self.units = units
            self.synthetic = False

    def path_ok(self, p: str) -> bool:
        if p in self.tracked:
            return True
        prefix = p if p.endswith("/") else p + "/"
        return any(t.startswith(prefix) for t in self.tracked)

    def unit_ok(self, u: str) -> bool:
        if u in self.units or (AGENT_UNITS_DIR + u) in self.tracked:
            return True
        if "@" in u:                                        # instance -> template
            base, _, suffix = u.partition("@")
            return f"{base}@.{suffix.rsplit('.', 1)[-1]}" in self.units
        return False

    def _load_routes(self, root: Path) -> None:
        for f in (root / "src").rglob("*.py"):
            try:
                src = f.read_text(errors="replace")
            except OSError:
                continue
            self.routes |= {route_key(m.group(1)) for m in ROUTE_SRC.finditer(src)}
            # APIRouter(prefix="/api/v1/x") + @router.get("/y") -> /api/v1/x/y
            for pre in ROUTER_PREFIX.findall(src):
                self.routes |= {route_key(pre + p) for p in DECORATED_PATH.findall(src)}

    def route_ok(self, r: str) -> bool:
        tail = r.split("{", 1)[1] if "{" in r else ""
        if tail and ("}" not in tail or "," in tail.split("}", 1)[0]):
            r = r.split("{", 1)[0]                            # doc brace shorthand: /api/x/{a,b} -> /api/x
        if r.startswith("/api/dispatch/"):
            r = r[len("/api/dispatch"):]                      # the runner proxies /api/dispatch/<dispatch route>
            if r.count("/") <= 3 or "<" in r:
                return True                                   # the proxy itself or a placeholder
        k = route_key(r)
        if k in self.routes or any(s.startswith(k + "/") for s in self.routes):
            return True
        # a concrete example (/api/v1/fids/DCA/X1) matches a parameterised route (/api/v1/fids/*/*)
        parts = k.split("/")
        return any(len(s.split("/")) >= len(parts) and all(a == b or b == "*" for a, b in zip(parts, s.split("/")))
                   for s in self.routes)

    def state(self, unit: str) -> str:
        """'enabled' / 'disabled' / '' (static, masked, unknown: never a miss)."""
        if unit not in self.states and not self.synthetic:
            out = ""
            for scope in (["--user"], []):
                out = _run(["systemctl", *scope, "is-enabled", unit]).strip()
                if out in ("enabled", "disabled"):
                    break
            self.states[unit] = out if out in ("enabled", "disabled") else ""
        return self.states.get(unit, "")

    def timer(self, name: str) -> dict[str, list[str]]:
        if name in self._timer_cache:
            return self._timer_cache[name]
        found: dict[str, list[str]] = {"OnCalendar": [], "OnUnitActiveSec": []}
        if not self.synthetic:
            for scope in (["--user"], []):
                out = _run(["systemctl", *scope, "cat", name])
                if not out.strip():
                    continue
                for ln in out.splitlines():
                    ln = ln.strip()
                    for key in found:
                        if ln.startswith(key + "="):
                            found[key].append(" ".join(ln[len(key) + 1:].split()))
                break
        self._timer_cache[name] = found
        return found


def check(root: Path, facts: LiveFacts) -> tuple[list[str], int, int]:
    docs = collect_docs(root)
    drift: list[str] = []
    n_claims = 0
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        for kind, no, value, extra in extract_claims(doc, root):
            n_claims += 1
            rel = extra.split("\t")[0]
            if kind == "path":
                ok = facts.path_ok(value)
                if not ok and rel.startswith(("skills/", ".agents/skills/")):   # skill-relative path
                    ok = facts.path_ok(rel.rsplit("/", 1)[0] + "/" + value)
                if not ok and (rel, value) not in seen:
                    seen.add((rel, value))
                    drift.append(f"[DRIFT] {rel}:{no}: path not tracked: {value}")
            elif kind == "unit":
                if facts.partial:
                    continue                                # snapshot lists loaded units only
                if not facts.unit_ok(value) and (rel, value) not in seen:
                    seen.add((rel, value))
                    drift.append(f"[DRIFT] {rel}:{no}: unit not installed: {value}")
            elif kind == "route":
                if not facts.route_ok(value) and (rel, value) not in seen:
                    seen.add((rel, value))
                    drift.append(f"[DRIFT] {rel}:{no}: route not in src/: {value}")
            elif facts.partial:
                continue                                    # snapshot mode: no enabled state, no schedules
            elif kind == "state":
                unit = extra.split("\t")[1]
                live = facts.state(unit)
                if live and live != value and (rel, unit + value) not in seen:
                    seen.add((rel, unit + value))
                    drift.append(f"[DRIFT] {rel}:{no}: {unit} documented {value}, live {live}")
            else:
                timer = extra.split("\t")[1]
                key, _, expr = value.partition("=")
                live = facts.timer(timer).get(key, [])
                if not facts.unit_ok(timer):
                    continue                                # already a unit miss
                if expr not in live:
                    drift.append(f"[DRIFT] {rel}:{no}: {timer} documents {key}={expr}; "
                                 f"live {key}={' / '.join(live) or '(none)'}")
    return drift, len(docs), n_claims


# ── public mirror parity and the 24-hour age ledger (2026-11-02) ─────────────
# The daily sweep found misses but nothing aged them: 21-22 misses sat open from 2026-10-28 to
# 2026-11-02 with the same p3 ntfy every morning, and nothing compared the public mirror at all.

MD_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def _git(root: Path, *args: str) -> str:
    return _run(["git", "-C", str(root), *args])


def public_parity(root: Path, ref: str, now: float, max_age_h: float) -> list[str]:
    """[DRIFT] lines for the public mirror `ref` (a local ref such as public/main; never fetched here):
    (a) a relative link in a public .md whose target the mirror does not carry (dropped by the scrub:
        a dead link in the published docs), and
    (b) a doc the mirror carries that changed on HEAD after the mirror commit, more than max_age_h ago."""
    out: list[str] = []
    if not _git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}").strip():
        return [f"[DRIFT] public:{ref}: ref not found -- the mirror cannot be compared"]
    tree = set(_git(root, "ls-tree", "-r", "--name-only", ref).split("\n")) - {""}
    dirs: set[str] = set()
    for t in tree:
        d = posixpath.dirname(t)
        while d:
            dirs.add(d)
            d = posixpath.dirname(d)
    for path in sorted(t for t in tree if t.endswith(".md")):
        text = _git(root, "show", f"{ref}:{path}")
        for no, line in enumerate(text.split("\n"), 1):
            for m in MD_LINK.finditer(line):
                target = m.group(1)
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith(("<", "#")):
                    continue
                if target.startswith("/"):
                    resolved = posixpath.normpath(target.lstrip("/"))
                else:
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
                if resolved not in tree and resolved not in dirs:
                    out.append(f"[DRIFT] public:{path}:{no}: link target not in the public mirror: {target}")
    pub_time = int(_git(root, "log", "-1", "--format=%ct", ref).strip() or 0)
    changed: dict[str, tuple[int, str]] = {}
    log = _git(root, "log", "--first-parent", f"--since=@{pub_time + 1}", "--format=@%ct %h",
               "--name-only", "HEAD")
    when, sha = 0, ""
    for ln in log.split("\n"):
        if ln.startswith("@"):
            t, _, sha = ln[1:].partition(" ")
            when = int(t)
        elif ln and when > pub_time and ln in tree and (ln.endswith(".md") or ln.startswith("docs/")):
            changed[ln] = (when, sha)          # log is newest first: the oldest unpublished change wins
    for path, (when, sha) in sorted(changed.items()):
        age_h = (now - when) / 3600
        if age_h > max_age_h:
            out.append(f"[DRIFT] public:{path}: changed on HEAD in {sha} {age_h:.0f}h ago, "
                       f"after the mirror commit; not published within {max_age_h:g}h")
    return out


def _ledger_key(line: str) -> str:
    """A miss's stable identity: the doc and the claim, never the line number (lines move)."""
    body = line[len("[DRIFT] "):]
    doc, _, rest = body.partition(":")
    rest = re.sub(r"^\d+:\s*", "", rest)
    return f"{doc}\t{rest}"


def age_ledger(path: Path, drift: list[str], now: float, max_age_h: float) -> list[str]:
    """Record first-seen times for current misses (resolved ones drop out); return [AGED] lines for
    misses open longer than max_age_h. Written atomically; an unreadable ledger starts fresh."""
    try:
        old = json.loads(path.read_text())
        if not isinstance(old, dict):
            old = {}
    except (OSError, ValueError):
        old = {}
    new: dict[str, float] = {}
    aged: list[str] = []
    for line in drift:
        key = _ledger_key(line)
        first = float(old.get(key, now))
        new.setdefault(key, first)
        age_h = (now - first) / 3600
        if age_h > max_age_h:
            since = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(first))
            aged.append(f"[AGED] open {age_h:.0f}h (since {since}): {line[len('[DRIFT] '):]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(new, indent=0, sort_keys=True))
    os.replace(tmp, path)
    return aged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--claims-only", metavar="DIR", help="root holding README.md, docs/, skills/")
    ap.add_argument("--live-facts", metavar="FILE", help="JSON facts instead of git/systemctl")
    ap.add_argument("--public-ref", metavar="REF",
                    help="also check the public mirror at this local git ref (e.g. public/main)")
    ap.add_argument("--age-ledger", metavar="FILE",
                    help="JSON first-seen ledger; misses open longer than --max-age-hours print [AGED]")
    ap.add_argument("--max-age-hours", type=float, default=24.0)
    ap.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)   # tests
    args = ap.parse_args(argv)
    now = args.now if args.now is not None else time.time()
    root = Path(args.claims_only or Path(__file__).resolve().parents[1]).resolve()
    if not root.is_dir():
        print(f"[FAIL] not a directory: {root}", file=sys.stderr)
        return 2
    facts = LiveFacts(root, Path(args.live_facts) if args.live_facts else None)
    drift, n_docs, n_claims = check(root, facts)
    if args.public_ref:
        drift += public_parity(root, args.public_ref, now, args.max_age_hours)
    for ln in drift:
        print(ln)
    summary = f"[SUMMARY] docs={n_docs} claims={n_claims} misses={len(drift)}"
    if args.age_ledger:
        aged = age_ledger(Path(args.age_ledger), drift, now, args.max_age_hours)
        for ln in aged:
            print(ln)
        summary += f" aged={len(aged)}"
    print(summary)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
