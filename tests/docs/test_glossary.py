"""2026-10-19 (batch G1 glossary): docs/glossary.yaml is the one source of vocabulary and logic rules.

- coverage: every all-caps word used 3 or more times in the current docs (undated docs/*.md, struck
  text removed) is defined or ignored with a reason;
- every logic rule names enforcing test files that exist;
- `render-glossary.py --check` is clean (generated files current) and rendering is deterministic;
- no identifier-shaped string (registration, hex, callsign, address, host, token) in any output;
- the compact persona rules block fits its budget; every deployment profile renders its slice;
- sources (G7, 2026-10-19): every entry has `source` (null, {cite, url} or {cite, path}); every URL
  is https on an allowlisted authoritative host, every path exists, rules and platform terms take
  paths only, GLOSSARY.md links each source and the persona rules block carries no link.
Offline; never prints an identifier (failure messages carry counts or vocabulary tokens only)."""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rg = _load("render_glossary", "scripts/render-glossary.py")
synth = _load("test_synthetic_identifiers_patterns", "tests/scripts/test_synthetic_identifiers.py")


@pytest.fixture(scope="module")
def data():
    return rg.load(REPO)


def test_yaml_is_valid(data):
    assert rg.validate(data, REPO) == []
    for section in ("acronyms", "platform", "rules"):
        assert rg.entries(data, section), section


def test_every_docs_all_caps_term_is_defined_or_ignored(data):
    missing = rg.uncovered(data, REPO)
    assert missing == [], ("add these to docs/glossary.yaml (define, or ignore with a reason): "
                           + ", ".join(f"{t} ({n} uses)" for t, n in missing))


def test_coverage_scanner_skips_struck_text_and_identifier_shapes(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "doc-claims-check.py").write_text((REPO / "scripts/doc-claims-check.py").read_text())
    (tmp_path / "docs" / "A.md").write_text("ZQXW ZQXW ZQXW ~~ZQXV ZQXV ZQXV~~ ABC1234 ABC1234 ABC1234\n")
    (tmp_path / "docs" / "OLD_2026-01-01.md").write_text("ZQXU ZQXU ZQXU\n")
    orig = rg.REPO
    try:
        rg.REPO = tmp_path
        counts = rg.doc_term_counts(tmp_path)
    finally:
        rg.REPO = orig
    assert counts["ZQXW"] == 3 and "ZQXV" not in counts and "ABC1234" not in counts and "ZQXU" not in counts


def test_every_rule_names_existing_test_files(data):
    rules = rg.entries(data, "rules")
    assert len(rules) >= 10
    for r in rules:
        assert r["enforced_by"], r["term"]
        for t in r["enforced_by"]:
            assert t.startswith("tests/") and (REPO / t).is_file(), (r["term"], t)
            assert re.search(r"^def test_|^    def test_", (REPO / t).read_text(), re.M), (r["term"], t)


def test_the_required_rules_are_present(data):
    text = " ".join(r["term"] + " " + r["definition"] for r in rg.entries(data, "rules")).lower()
    for needle in ("fids", "swim-only", "ads-b", "oooi", "timestamps", "utc", "union", "never printed",
                   "unsigned", "operator approval", "load-aware"):
        assert needle in text, needle


def test_render_check_is_clean(capsys):
    assert rg.main(["--check", "--repo", str(REPO)]) == 0, capsys.readouterr().err


def test_rendering_is_deterministic_and_order_independent(data):
    first = rg.outputs(data)
    assert rg.outputs(rg.load(REPO)) == first
    shuffled = copy.deepcopy(data)
    for section in ("acronyms", "platform"):
        shuffled[section] = list(reversed(shuffled[section]))
    assert rg.render_markdown(shuffled) == first[rg.GLOSSARY_MD]
    assert rg.render_pamphlet(shuffled) == rg.render_pamphlet(data)
    for text in list(first.values()) + [rg.render_pamphlet(data)]:
        assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}", text), "no dates or clock times in generated text"


def test_glossary_md_is_alphabetical_within_sections():
    md = (REPO / rg.GLOSSARY_MD).read_text()
    for chunk in re.split(r"^## ", md, flags=re.M)[1:]:
        terms = re.findall(r"^\| \*\*(.+?)\*\*", chunk, re.M)
        assert terms == sorted(terms, key=lambda t: (t.casefold(), t)), chunk.split("\n", 1)[0]


def test_compact_rules_block_fits_the_persona_budget(data):
    block = rg.render_rules(data)
    lines = block.splitlines()
    assert len(lines) <= rg.RULES_MAX_LINES
    assert len(lines) == 1 + len(rg.entries(data, "rules"))
    assert len(block.split()) * 4 / 3 <= 250, "about 250 prompt tokens at most (design D4)"
    assert "**" not in block and "`" not in block and "|" not in block, "plain text for the local model"


# Identifier shapes. N-number shape reused from tests/scripts/test_synthetic_identifiers.py.
SHAPES = {
    "us-registration": synth.N_REG,
    "icao-hex": re.compile(r"(?<![A-Za-z0-9])(?=[0-9A-Fa-f]*[0-9])(?=[0-9A-Fa-f]*[A-Fa-f])[0-9A-Fa-f]{6}(?![A-Za-z0-9])"),
    "callsign": re.compile(r"(?<![A-Za-z0-9-])[A-Z]{3}[0-9]{2,4}[A-Z]?(?![A-Za-z0-9])"),
    "q-mark": re.compile(r"(?<![A-Za-z0-9])Q(?=[A-Z0-9]*[0-9])[A-Z0-9]{5}(?![A-Za-z0-9])"),
    "ipv4": re.compile(r"(?<![0-9.])\d{1,3}(?:\.\d{1,3}){3}(?![0-9.])"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "url-or-host": re.compile(r"https?://|\b[a-z0-9-]+\.(?:com|net|org|gov|io|mil|ai)\b"),
    "token": re.compile(r"\bctdc_[A-Za-z0-9]|\b(?:sk|ghp|gho|pk)_[A-Za-z0-9]{8,}"),
    "account": re.compile(r"\bctdc-agent-[a-z]|\bcorporatetraveldc\b"),
}


def _source_urls(data) -> list[str]:
    return sorted({e["source"]["url"] for sec, _ in rg.SECTIONS for e in rg.entries(data, sec)
                   if e.get("source") and "url" in e["source"]}, key=len, reverse=True)


def _without_source_urls(text: str, data) -> str:
    """Source URLs are the one allowed host-shaped text (G7); everything else is still scanned."""
    for u in _source_urls(data):
        text = text.replace(_md_target(u), " ").replace(u, " ")
    return text


def _md_target(u: str) -> str:
    return u.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _generated(data) -> dict[str, str]:
    out = {rg.SOURCE: (REPO / rg.SOURCE).read_text(), "pamphlet": rg.render_pamphlet(data)}
    out.update(rg.outputs(data))
    for name in rg.discover_profiles(REPO):
        out[f"profile:{name}"] = rg.render_profile(data, name, "json", REPO)
    return out


def test_no_identifier_shaped_strings(data):
    hits = {}
    for where, text in _generated(data).items():
        body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#")) if where == rg.SOURCE else text
        body = _without_source_urls(body, data)
        for shape, rx in SHAPES.items():
            n = len(rx.findall(body))
            if n:
                hits[f"{where}:{shape}"] = n            # counts only: never echo a match
    assert hits == {}, hits


def test_generated_text_passes_the_vault_scrub_gate(data):
    sys.path.insert(0, str(REPO / "src"))
    from second_brain.scrub_gate import scan
    for where, text in _generated(data).items():
        assert scan(text) == [], where


def test_every_profile_gets_a_slice(data):
    profiles = rg.discover_profiles(REPO)
    assert {"base", "cfo", "chauffeur-regulated"} <= set(profiles)
    scopes = {n: rg.profile_scopes(data, json.loads(p.read_text())) for n, p in profiles.items()}
    for n, s in scopes.items():
        assert {"platform", "agents"} <= s <= set(data["scopes"]), n
        md = rg.render_profile(data, n, "md", REPO)
        assert md.startswith("# Glossary -- profile"), n
    assert "aviation" not in scopes["cfo"] and "aviation" not in scopes["base"]
    assert {"aviation", "rail", "weather"} <= scopes["chauffeur-regulated"]
    cfo = json.loads(rg.render_profile(data, "cfo", "json", REPO))
    # 2026-10-20 (CFO, EA and CoS profiles): the CFO slice gains the finance, governance and office
    # scopes; it still carries nothing from aviation, rail, weather or maritime.
    assert cfo["acronyms"] and all(e["scope"] in ("platform", "agents", "finance", "governance", "office")
                                   for sec in ("acronyms", "platform", "rules") for e in cfo[sec])
    assert any(e["scope"] == "finance" for e in cfo["acronyms"])
    assert "FIDS" not in rg.render_profile(data, "cfo", "rules", REPO)
    assert {"cfo", "executive-assistant", "chief-of-staff"} <= set(profiles)
    assert "finance" in scopes["cfo"] and "scheduling" in scopes["executive-assistant"]
    assert "governance" in scopes["chief-of-staff"] and "aviation" not in scopes["chief-of-staff"]


def test_profile_scope_map_names_only_real_modules_and_sources(data):
    """A mapped module or source that no profile and no schema knows is a typo."""
    known = set()
    for p in rg.discover_profiles(REPO).values():
        d = json.loads(p.read_text())
        for k in ("modules", "sources"):
            known |= set(d[k].get("enabled") or []) | set(d[k].get("disabled") or [])
    schema = (REPO / "docs/deployment-profiles/schemas/profile.schema.json").read_text()
    for kind in ("modules", "sources"):
        for name in data["profile_scopes"][kind]:
            assert name in known or f'"{name}"' in schema or name == "local_ais", (kind, name)


# ---------------------------------------------------------------- sources (G7, 2026-10-19)
def test_every_entry_has_a_source_field_of_the_right_shape(data):
    for sec, _ in rg.SECTIONS:
        for e in rg.entries(data, sec):
            assert "source" in e, (sec, e["term"])
            s = e["source"]
            assert s is None or (isinstance(s, dict) and set(s) in rg.SOURCE_KEYS), (sec, e["term"])
            if s:
                assert isinstance(s["cite"], str) and s["cite"].strip() and "\n" not in s["cite"], (sec, e["term"])


def test_every_source_url_is_https_on_an_authoritative_host(data):
    urls = _source_urls(data)
    assert len(urls) >= 100
    for u in urls:
        assert u.startswith("https://"), u
        assert rg.source_host_allowed(u), u
    for host, why in rg.SOURCE_DOMAINS.items():
        assert why.strip() and "/" not in host and host == host.lower(), host
    assert rg.source_host_allowed("https://www.faa.gov/x") and rg.source_host_allowed("https://aa.data.faa.gov/x")
    assert not rg.source_host_allowed("https://faa.gov.example.net/x")
    assert not rg.source_host_allowed("https://en.wikipedia.org/wiki/NOTAM")


def test_every_source_path_exists_and_rules_and_platform_terms_use_paths(data):
    paths = 0
    for sec, _ in rg.SECTIONS:
        for e in rg.entries(data, sec):
            s = e.get("source")
            if not s:
                continue
            if sec in ("rules", "platform"):
                assert "path" in s, (sec, e["term"])
            if "path" in s:
                paths += 1
                assert (REPO / s["path"]).is_file(), (sec, e["term"])
    assert paths >= len(rg.entries(data, "rules"))


def test_faa_terms_link_the_faa_definitions(data):
    by = {e["term"]: e for e in rg.entries(data, "acronyms")}
    for term in ("TFR", "NOTAM", "STA", "ETA", "GDP", "EDCT", "ARTCC", "ADS-B", "MFX"):
        assert "pcg_html" in by[term]["source"]["url"], term          # FAA Pilot/Controller Glossary
    assert "foa_html/chap18_section_25" in by["TBFM"]["source"]["url"]  # FAA JO 7210.3, TBFM
    assert "section-91.141" in by["VIP TFR"]["source"]["url"]
    assert by["LADD"]["source"]["url"].startswith("https://www.faa.gov/")


def test_mfx_is_defined_from_the_tbfm_meter_fix(data):
    mfx = next(e for e in rg.entries(data, "acronyms") if e["term"] == "MFX")
    assert "not yet defined" not in mfx["expansion"].lower() and "placeholder" not in mfx["definition"].lower()
    assert mfx["scope"] == "aviation" and "meter fix" in mfx["definition"]
    assert "METERING_FIX" in mfx["source"]["url"]
    parser = (REPO / "src/ingest/parsers/tbfm_parser.py").read_text()
    assert '("eta_mfx", "mfx")' in parser and '("eta_rwy", "rwy")' in parser   # the tags the entry describes


def test_validate_rejects_bad_sources(data):
    def errs_with(section, idx, source, drop=False):
        d = copy.deepcopy(data)
        if drop:
            del d[section][idx]["source"]
        else:
            d[section][idx]["source"] = source
        return [e for e in rg.validate(d, REPO) if "source" in e]
    assert errs_with("acronyms", 0, None) == []
    assert errs_with("acronyms", 0, None, drop=True)
    assert errs_with("acronyms", 0, {"cite": "x", "url": "http://www.faa.gov/"})
    assert errs_with("acronyms", 0, {"cite": "x", "url": "https://example.com/"})
    assert errs_with("acronyms", 0, {"cite": "x", "path": "no/such/file.py"})
    assert errs_with("acronyms", 0, {"cite": "x", "path": "../etc/passwd"})
    assert errs_with("acronyms", 0, {"cite": "", "url": "https://www.faa.gov/"})
    assert errs_with("acronyms", 0, {"cite": "x", "url": "https://www.faa.gov/", "path": "README.md"})
    assert errs_with("acronyms", 0, "https://www.faa.gov/")
    assert errs_with("rules", 0, {"cite": "x", "url": "https://www.faa.gov/"})        # rules take paths
    assert errs_with("platform", 0, {"cite": "x", "url": "https://www.faa.gov/"})     # so do platform terms
    assert errs_with("rules", 0, {"cite": "x", "path": "README.md"}) == []


def test_glossary_md_links_sources_and_the_rules_block_does_not(data):
    md = (REPO / rg.GLOSSARY_MD).read_text()
    for u in _source_urls(data):
        assert f"]({_md_target(u)})" in md
    assert "](../scripts/relay-lint.py)" in md
    n_none = sum(1 for sec, _ in rg.SECTIONS for e in rg.entries(data, sec) if not e.get("source"))
    assert md.count("_none found_") == n_none + 1                        # one per sourceless row + the legend
    block = rg.render_rules(data)
    assert "http" not in block and "](" not in block and "www." not in block
    assert (REPO / "src/common/glossary_rules.txt").read_text() == block
