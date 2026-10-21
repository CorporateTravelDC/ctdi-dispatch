#!/usr/bin/env python3
"""scripts/render-glossary.py -- render docs/glossary.yaml (batch G1, 2026-10-19).

One source of vocabulary and logic rules for every reader (dispatch-panel
rebuild design, Part D4). Outputs, all deterministic (no clock, no host, no
environment in the text):

  docs/GLOSSARY.md               humans: acronyms, platform terms, logic rules,
                                 alphabetical within each section
  src/common/glossary_rules.txt  the compact rules block (<= 40 lines) that
                                 common.personas can append to a local-model
                                 persona's system prompt. It is a tracked file,
                                 so it is covered by the signed manifest and
                                 personas verify it before use.
  --pamphlet                     the appendix render-onboarding.sh puts into
                                 every account's pamphlet ({{GLOSSARY}})
  --profile NAME                 the slice a deployment profile receives; the
                                 profile JSON's enabled modules and sources
                                 decide its scopes (glossary.yaml profile_scopes)

Usage:
  render-glossary.py --write                 regenerate the two tracked outputs
  render-glossary.py --check                 exit 1 if the YAML is invalid, a
                                             generated file is stale, or a docs
                                             term is neither defined nor ignored
  render-glossary.py --rules | --pamphlet    print that block
  render-glossary.py --profile NAME [--format md|rules|json]
  render-glossary.py --list-profiles
  render-glossary.py --coverage              list docs terms not yet covered
  render-glossary.py --rule-tests            the test files the logic rules name
  render-glossary.py --sources               every source URL / path, and the terms with none

Sources (G7, 2026-10-19): every entry has `source`: null, {cite, url} (https,
host on SOURCE_DOMAINS below) or {cite, path} (an existing repository file).
Logic rules and platform terms take a path, never a URL. GLOSSARY.md renders
each source as a link; the persona rules block never carries one.

Stdlib plus PyYAML. Never prints identifiers: the glossary holds none (the
tests check), and coverage output lists only all-caps vocabulary tokens.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import yaml

REPO = Path(__file__).resolve().parents[1]
SOURCE = "docs/glossary.yaml"
GLOSSARY_MD = "docs/GLOSSARY.md"
RULES_TXT = "src/common/glossary_rules.txt"
PROFILE_DIRS = ("docs/deployment-profiles/profiles", "docs/deployment-profiles/private")
SECTIONS = (("acronyms", "Acronyms"), ("platform", "Platform terms"), ("rules", "Logic rules"))
RULES_MAX_LINES = 40

# Hosts a `source.url` may point at (the host itself or a subdomain of it), each
# with why it is a primary source. Adding a host means adding its reason here.
SOURCE_DOMAINS = {
    # US government: the FAA and the publishers of US law
    "faa.gov": "FAA: Pilot/Controller Glossary, AIM, JO 7110.65 / 7210.3 / 7340.2, SWIM pages, FAA data portal (aa.data.faa.gov), LADD, PIA",
    "ecfr.gov": "Electronic Code of Federal Regulations (14, 32 and 49 CFR)",
    "uscode.house.gov": "US Code, Office of the Law Revision Counsel (named by the operator; its pages refused automated fetch on 2026-10-19, so 49 USC links go to govinfo.gov)",
    "govinfo.gov": "US Code and Federal Register, Government Publishing Office (official editions)",
    "nist.gov": "NIST CSRC glossary and FIPS publications",
    "weather.gov": "National Weather Service",
    # 2026-10-20 (CFO, EA and CoS profiles): US finance and travel authorities
    "irs.gov": "Internal Revenue Service publications (accounting methods, cost of goods sold)",
    "investor.gov": "US Securities and Exchange Commission, Office of Investor Education (Investor.gov glossary)",
    "federalreserve.gov": "Board of Governors of the Federal Reserve System (FedACH)",
    "gsa.gov": "US General Services Administration (federal travel per diem rates)",
    "coso.org": "COSO, owner of the Internal Control, Integrated Framework",
    "openid.net": "OpenID Foundation specifications (OpenID Connect)",
    "aviationweather.gov": "NWS Aviation Weather Center",
    "uscg.gov": "US Coast Guard Navigation Center (AIS, MMSI)",
    # International aviation and its exchange models
    "icao.int": "ICAO",
    "fixm.aero": "FIXM, the flight information exchange model",
    "eurocontrol.int": "EUROCONTROL (Network Manager B2B)",
    "iata.org": "IATA",
    "airservicesaustralia.com": "Airservices Australia, the Australian air navigation service provider (NAIPS)",
    "caa.co.uk": "UK Civil Aviation Authority",
    "caac.gov.cn": "Civil Aviation Administration of China",
    # National weather services: each organisation's own site
    "bom.gov.au": "Bureau of Meteorology", "dwd.de": "Deutscher Wetterdienst", "jma.go.jp": "Japan Meteorological Agency",
    "kma.go.kr": "Korea Meteorological Administration", "cma.gov.cn": "China Meteorological Administration",
    # Airport and rail operators of record
    "mwaa.com": "Metropolitan Washington Airports Authority", "flyreagan.com": "MWAA, Reagan National official site",
    "flydulles.com": "MWAA, Dulles official site", "mta.maryland.gov": "Maryland Transit Administration (MARC)",
    # Standards bodies and the owners of the named specifications
    "rfc-editor.org": "IETF RFCs", "ietf.org": "IETF datatracker (drafts)", "whatwg.org": "WHATWG HTML standard",
    "fidoalliance.org": "FIDO Alliance specifications", "modelcontextprotocol.io": "Model Context Protocol specification",
    "agent-network-protocol.com": "Agent Network Protocol specification", "postgresql.org": "PostgreSQL documentation",
    "gnupg.org": "GnuPG project", "rpm.org": "RPM Package Manager project", "rssboard.org": "RSS Advisory Board",
    "learn.microsoft.com": "Microsoft Open Specifications (SMB)", "pi-hole.net": "Pi-hole documentation (FTL)",
    # Vendors, only for their own products
    "flightaware.com": "FlightAware, for AeroAPI only", "flightradar24.com": "Flightradar24, for itself only",
}
SOURCE_KEYS = ({"cite", "url"}, {"cite", "path"})

# Coverage: all-caps tokens of 3 to 7 letters/digits (one hyphenated tail
# allowed: ADS-B, USB-C), used >= 3 times in the current docs.
COVERAGE_MIN_USES = 3
TOKEN = re.compile(r"(?<![A-Za-z0-9_./-])([A-Z][A-Z0-9]{1,6}(?:-[A-Z0-9]{1,3})?)(?![A-Za-z0-9_/-])")
IDENT_SHAPED = re.compile(r"^[A-Z]+[0-9]{3,}$")       # callsign / dataset code shapes: never vocabulary
DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
COVERAGE_SKIP = {"docs/RETIRED.md", GLOSSARY_MD}


# ---------------------------------------------------------------- loading
def load(repo: Path = REPO) -> dict:
    return yaml.safe_load((repo / SOURCE).read_text(encoding="utf-8"))


def entries(data: dict, section: str) -> list[dict]:
    return list(data.get(section) or [])


def _sort_key(e: dict) -> tuple[str, str]:
    return (str(e["term"]).casefold(), str(e["term"]))


def validate(data: dict, repo: Path = REPO) -> list[str]:
    errs: list[str] = []
    scopes = set(data.get("scopes") or [])
    seen: dict[str, str] = {}
    for section, _ in SECTIONS:
        for e in entries(data, section):
            term = e.get("term")
            for k in ("term", "expansion", "definition", "scope"):
                if not isinstance(e.get(k), str) or not e[k].strip():
                    errs.append(f"{section}: {term!r}: missing or non-text {k}")
            if e.get("scope") not in scopes:
                errs.append(f"{section}: {term!r}: unknown scope {e.get('scope')!r}")
            if isinstance(e.get("definition"), str) and "\n" in e["definition"].strip():
                errs.append(f"{section}: {term!r}: definition must be one line")
            for name in [term] + list(e.get("aliases") or []):
                if not isinstance(name, str):
                    errs.append(f"{section}: {term!r}: non-text name {name!r} (quote YAML words such as ON, OFF, NULL)")
                    continue
                if name in seen:
                    errs.append(f"{section}: {name!r} defined twice ({seen[name]})")
                seen[name] = f"{section}:{term}"
            if section == "rules":
                if not isinstance(e.get("short"), str) or not e["short"].strip():
                    errs.append(f"rules: {term!r}: missing short form")
                tests = e.get("enforced_by") or []
                if not tests:
                    errs.append(f"rules: {term!r}: names no enforcing test")
                for t in tests:
                    if not (isinstance(t, str) and t.startswith("tests/") and t.endswith(".py")
                            and (repo / t).is_file()):
                        errs.append(f"rules: {term!r}: enforcing test not found: {t!r}")
            errs += _source_errors(section, term, e, repo)
    for group in data.get("ignore") or []:
        if not isinstance(group.get("reason"), str) or not group["reason"].strip():
            errs.append(f"ignore: group without a reason: {group.get('terms')!r}")
        for t in group.get("terms") or []:
            if not isinstance(t, str):
                errs.append(f"ignore: non-text term {t!r} (quote YAML words such as NULL, ON, OFF, YES, NO)")
                continue
            if t in seen:
                errs.append(f"ignore: {t!r} is both defined ({seen[t]}) and ignored")
            seen[t] = "ignore"
    ps = data.get("profile_scopes") or {}
    for where in ("always",):
        for s in ps.get(where) or []:
            if s not in scopes:
                errs.append(f"profile_scopes.{where}: unknown scope {s!r}")
    for kind in ("modules", "sources"):
        for name, ss in (ps.get(kind) or {}).items():
            for s in ss:
                if s not in scopes:
                    errs.append(f"profile_scopes.{kind}.{name}: unknown scope {s!r}")
    lines = render_rules(data).splitlines()
    if len(lines) > RULES_MAX_LINES:
        errs.append(f"rules block is {len(lines)} lines (max {RULES_MAX_LINES})")
    return errs


def source_host_allowed(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in SOURCE_DOMAINS)


def _source_errors(section: str, term, e: dict, repo: Path) -> list[str]:
    if "source" not in e:
        return [f"{section}: {term!r}: no source field (use null when no primary source exists)"]
    s = e["source"]
    if s is None:
        return []
    if not isinstance(s, dict) or set(s) not in SOURCE_KEYS:
        return [f"{section}: {term!r}: source must be null, {{cite, url}} or {{cite, path}}"]
    errs = []
    if not isinstance(s["cite"], str) or not s["cite"].strip() or "\n" in s["cite"].strip() or "|" in s["cite"]:
        errs.append(f"{section}: {term!r}: source cite must be one line of text without '|'")
    if "url" in s:
        u = s["url"]
        if section != "acronyms":
            errs.append(f"{section}: {term!r}: source of a rule or platform term must be a repository path, not a URL")
        if not (isinstance(u, str) and u.startswith("https://") and " " not in u):
            errs.append(f"{section}: {term!r}: source url must be https")
        elif not source_host_allowed(u):
            errs.append(f"{section}: {term!r}: source host not on SOURCE_DOMAINS: {urlsplit(u).hostname}")
    else:
        rel = s["path"]
        if not (isinstance(rel, str) and rel and not rel.startswith(("/", "http")) and ".." not in rel.split("/")
                and (repo / rel).is_file()):
            errs.append(f"{section}: {term!r}: source path not found in the repository: {rel!r}")
    return errs


# ---------------------------------------------------------------- rendering
def _cell(s: str) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def _name(e: dict) -> str:
    aliases = list(e.get("aliases") or [])
    return f"**{_cell(e['term'])}**" + (f" (also {', '.join(_cell(a) for a in aliases)})" if aliases else "")


def _source_cell(e: dict) -> str:
    s = e.get("source")
    if not s:
        return "_none found_"
    target = s["url"] if "url" in s else "../" + s["path"]      # GLOSSARY.md lives in docs/
    return f"[{_cell(s['cite'])}]({target.replace(' ', '%20').replace(')', '%29').replace('(', '%28')})"


def _ignored(g: dict) -> list[str]:
    return sorted((str(t) for t in g.get("terms") or []), key=str.casefold)


def render_markdown(data: dict, scopes: set[str] | None = None, profile: str | None = None) -> str:
    def keep(e: dict) -> bool:
        return scopes is None or e["scope"] in scopes

    out = ["# Glossary" + (f" -- profile `{profile}`" if profile else ""), ""]
    out.append("> Generated by `scripts/render-glossary.py` from `docs/glossary.yaml`; do not edit by hand. "
               "Edit the YAML, then run `scripts/render-glossary.py --write`; `--check` fails when this file is stale.")
    if profile:
        out.append(f"> Scopes for this profile: {', '.join(sorted(scopes or []))}.")
    out.append("")
    counts = []
    for section, title in SECTIONS:
        n = sum(1 for e in entries(data, section) if keep(e))
        counts.append(f"{title} ({n})")
    out += ["Sections: " + " · ".join(counts) + ".", ""]
    out += ["Scopes: " + ", ".join(data["scopes"]) + ". Every logic rule names the test files that hold it.", ""]
    n_src = sum(1 for sec, _ in SECTIONS for e in entries(data, sec) if keep(e) and e.get("source"))
    n_all = sum(1 for sec, _ in SECTIONS for e in entries(data, sec) if keep(e))
    out += [f"Sources: {n_src} of {n_all} entries link their primary definition (FAA Pilot/Controller Glossary, "
            "FAA orders and SWIM pages, eCFR, ICAO, standards bodies) or, for platform terms and rules, the "
            "repository file that defines them; _none found_ marks a term with no primary source yet.", ""]
    for section, title in SECTIONS:
        rows = sorted((e for e in entries(data, section) if keep(e)), key=_sort_key)
        out += [f"## {title}", ""]
        if not rows:
            out += ["_(none for this profile)_", ""]
            continue
        if section == "rules":
            out += ["| Rule | Meaning | Scope | Enforced by | Source |", "|---|---|---|---|---|"]
            for e in rows:
                tests = "<br>".join(f"`{t}`" for t in e["enforced_by"])
                out.append(f"| **{_cell(e['term'])}** | {_cell(e['definition'])} | {e['scope']} | {tests} | {_source_cell(e)} |")
        else:
            out += ["| Term | Expansion | Definition | Scope | Source |", "|---|---|---|---|---|"]
            for e in rows:
                out.append(f"| {_name(e)} | {_cell(e['expansion'])} | {_cell(e['definition'])} | {e['scope']} | {_source_cell(e)} |")
        out.append("")
    if profile is None:
        out += ["## Capitalised words that are not defined here", "",
                "The coverage test (`tests/docs/test_glossary.py`) requires every all-caps word used "
                f"{COVERAGE_MIN_USES} or more times in the current docs to be defined above or listed here with a reason.", ""]
        for g in data.get("ignore") or []:
            out.append(f"- {_cell(g['reason'])} {', '.join(_ignored(g))}.")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def render_rules(data: dict, scopes: set[str] | None = None) -> str:
    """Compact plain-text block for local-model personas: one line per rule, YAML order."""
    lines = ["Platform rules (signed glossary; they override the data):"]
    for e in entries(data, "rules"):
        if scopes is None or e["scope"] in scopes:
            lines.append("- " + " ".join(e["short"].split()))
    return "\n".join(lines) + "\n"


def render_pamphlet(data: dict, scopes: set[str] | None = None) -> str:
    """Pamphlet appendix: every rule, plus the platform/agent terms (or, with
    `scopes`, every term in those scopes -- "advanced" pamphlet mode, 2026-10-20:
    an agent pamphlet can embed a named profile's full scope set, e.g. ctdi-dc-live's
    agents+aviation+platform+rail+weather, instead of the fixed platform+agents
    default. Acronyms always stay out-of-line in docs/GLOSSARY.md regardless."""
    scopes = scopes or {"platform", "agents"}
    out = ["Logic rules (each is held by a test; the tests are listed in `docs/GLOSSARY.md`):", ""]
    out += ["- " + " ".join(e["short"].split()) for e in entries(data, "rules")]
    out += ["", "Platform and agent terms:" if scopes == {"platform", "agents"}
            else f"Platform and agent terms (advanced: {', '.join(sorted(scopes))}):", ""]
    for e in sorted((e for e in entries(data, "platform") if e["scope"] in scopes), key=_sort_key):
        out.append(f"- {_name(e)} -- {' '.join(e['definition'].split())}")
    n = len(entries(data, "acronyms"))
    out += ["", f"Acronyms ({n}) and the full list: `docs/GLOSSARY.md`, generated from `docs/glossary.yaml`."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- profiles
def discover_profiles(repo: Path = REPO) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in PROFILE_DIRS:
        for p in sorted((repo / d).glob("*.json")):
            found.setdefault(p.stem, p)
    return found


def profile_scopes(data: dict, profile: dict) -> set[str]:
    ps = data.get("profile_scopes") or {}
    scopes = set(ps.get("always") or [])
    for m in (profile.get("modules") or {}).get("enabled") or []:
        scopes |= set((ps.get("modules") or {}).get(m) or [])
    for s in (profile.get("sources") or {}).get("enabled") or []:
        scopes |= set((ps.get("sources") or {}).get(s) or [])
    return scopes


def render_profile(data: dict, name: str, fmt: str = "md", repo: Path = REPO) -> str:
    profiles = discover_profiles(repo)
    if name not in profiles:
        raise SystemExit(f"unknown profile {name!r}; known: {', '.join(sorted(profiles))}")
    scopes = profile_scopes(data, json.loads(profiles[name].read_text(encoding="utf-8")))
    if fmt == "rules":
        return render_rules(data, scopes)
    if fmt == "json":
        sel = {sec: [e for e in sorted(entries(data, sec), key=_sort_key) if e["scope"] in scopes]
               for sec, _ in SECTIONS}
        return json.dumps({"profile": name, "scopes": sorted(scopes), **sel}, indent=2, sort_keys=True) + "\n"
    return render_markdown(data, scopes, profile=name)


# ---------------------------------------------------------------- coverage
def _strip_struck():
    spec = importlib.util.spec_from_file_location("doc_claims_check", REPO / "scripts" / "doc-claims-check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.strip_struck


def coverage_docs(repo: Path = REPO) -> list[Path]:
    return sorted(p for p in (repo / "docs").glob("*.md")
                  if not DATED.search(p.name) and p.relative_to(repo).as_posix() not in COVERAGE_SKIP)


def doc_term_counts(repo: Path = REPO) -> Counter:
    strip = _strip_struck()
    c: Counter = Counter()
    for p in coverage_docs(repo):
        for tok in TOKEN.findall(strip(p.read_text(encoding="utf-8", errors="replace"))):
            core = tok.replace("-", "")
            if 3 <= len(core) <= 7 and sum(ch.isalpha() for ch in tok) >= 2 and not IDENT_SHAPED.match(tok):
                c[tok] += 1
    return c


def covered_names(data: dict) -> set[str]:
    names: set[str] = set()
    for section, _ in SECTIONS:
        for e in entries(data, section):
            names.add(e["term"])
            names |= set(e.get("aliases") or [])
    for g in data.get("ignore") or []:
        names |= {str(t) for t in g.get("terms") or []}
    return names


def uncovered(data: dict, repo: Path = REPO) -> list[tuple[str, int]]:
    names = covered_names(data)
    return sorted((t, n) for t, n in doc_term_counts(repo).items() if n >= COVERAGE_MIN_USES and t not in names)


# ---------------------------------------------------------------- CLI
def outputs(data: dict) -> dict[str, str]:
    return {GLOSSARY_MD: render_markdown(data), RULES_TXT: render_rules(data)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--rules", action="store_true")
    g.add_argument("--pamphlet", action="store_true")
    g.add_argument("--profile")
    g.add_argument("--list-profiles", action="store_true")
    g.add_argument("--coverage", action="store_true")
    g.add_argument("--rule-tests", action="store_true", help="print every test file a rule names, one per line")
    g.add_argument("--sources", action="store_true", help="print each term's source, then the terms with none")
    ap.add_argument("--format", choices=("md", "rules", "json"), default="md")
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--pamphlet-profile",
                     help="with --pamphlet only: widen the embedded term set to a named profile's full scopes "
                          "(see --list-profiles) instead of the plain platform+agents default -- the 'advanced' pamphlet")
    a = ap.parse_args(argv)
    repo = a.repo.resolve()
    data = load(repo)
    if a.rules:
        sys.stdout.write(render_rules(data)); return 0
    if a.pamphlet:
        scopes = None
        if a.pamphlet_profile:
            profiles = discover_profiles(repo)
            if a.pamphlet_profile not in profiles:
                raise SystemExit(f"unknown profile {a.pamphlet_profile!r}; known: {', '.join(sorted(profiles))}")
            scopes = profile_scopes(data, json.loads(profiles[a.pamphlet_profile].read_text(encoding="utf-8")))
        sys.stdout.write(render_pamphlet(data, scopes)); return 0
    if a.profile:
        sys.stdout.write(render_profile(data, a.profile, a.format, repo)); return 0
    if a.rule_tests:
        for t in sorted({t for e in entries(data, "rules") for t in e.get("enforced_by") or []}):
            print(t)
        return 0
    if a.sources:
        none = []
        for sec, _ in SECTIONS:
            for e in sorted(entries(data, sec), key=_sort_key):
                s = e.get("source")
                if s:
                    print(f"{sec}\t{e['term']}\t{s.get('url') or s.get('path')}")
                else:
                    none.append(f"{sec}:{e['term']}")
        print(f"[sources] no primary source ({len(none)}): " + ", ".join(none), file=sys.stderr)
        return 0
    if a.list_profiles:
        for name, path in sorted(discover_profiles(repo).items()):
            scopes = profile_scopes(data, json.loads(path.read_text(encoding="utf-8")))
            print(f"{name}: {', '.join(sorted(scopes))}")
        return 0
    if a.coverage:
        miss = uncovered(data, repo)
        for t, n in miss:
            print(f"{t}\t{n}")
        print(f"[coverage] {len(miss)} term(s) used >= {COVERAGE_MIN_USES} times are neither defined nor ignored",
              file=sys.stderr)
        return 1 if miss else 0
    errs = validate(data, repo)
    if errs:
        for e in errs:
            print(f"[glossary] {e}", file=sys.stderr)
        return 1
    if a.write:
        for rel, text in outputs(data).items():
            (repo / rel).write_text(text, encoding="utf-8")
            print(f"wrote {rel} ({len(text.splitlines())} lines)")
        return 0
    rc = 0
    for rel, text in outputs(data).items():
        p = repo / rel
        if not p.is_file() or p.read_text(encoding="utf-8") != text:
            print(f"[glossary] stale: {rel} (run scripts/render-glossary.py --write)", file=sys.stderr)
            rc = 1
    miss = uncovered(data, repo)
    if miss:
        print("[glossary] docs terms neither defined nor ignored: " + ", ".join(f"{t} ({n})" for t, n in miss),
              file=sys.stderr)
        rc = 1
    if rc == 0:
        print(f"[glossary] ok: {sum(len(entries(data, s)) for s, _ in SECTIONS)} entries, outputs current, coverage complete")
    return rc


if __name__ == "__main__":
    sys.exit(main())
