"""Shared all-origin catalog and future-source contract; no live state."""
from pathlib import Path
import importlib.util,json,shutil
import pytest
from web.routes import agent_gateway as route
REPO=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("neutral_catalog",REPO/"scripts/skills-catalog.py")
catalog=importlib.util.module_from_spec(spec);spec.loader.exec_module(catalog)

def test_all_registered_packages_have_common_source_contract():
    result=catalog.build(REPO)
    assert result==json.loads((REPO/"skills/catalog.json").read_text())
    assert len(result["skills"])>=26
    assert {"Anthropic","CorporateTravelDC","UNVERIFIED"}=={r["original_source"] for r in result["skills"]}
    sg=catalog.grants_module(REPO)
    assert all(e["source"]=="signed" for e in sg.catalog(str(REPO),"/nonexistent").values())
    assert json.loads((REPO/"skills/morning/PROVENANCE.json").read_text())["original_source_status"]=="unverified-earliest-origin"
    for name,pin in sg.read_pins(str(REPO)).items():
        assert json.loads((REPO/"skills"/name/"PROVENANCE.json").read_text())["upstream_tree_sha256"]==pin

@pytest.mark.parametrize("defect",["new-skill","missing-provenance","missing-needs","modified-upstream","extra-upstream","divergent-mirror"])
def test_new_or_changed_skill_cannot_silently_escape_contract(tmp_path,defect):
    r=tmp_path/"repo";(r/"scripts/lib").mkdir(parents=True)
    shutil.copyfile(REPO/"scripts/lib/skill_grants.py",r/"scripts/lib/skill_grants.py")
    package=r/"skills/example";package.mkdir(parents=True);(package/"SKILL.md").write_text("safe")
    (r/"skills/skill-capabilities.txt").write_text("example read-only\n")
    meta={"name":"example","original_source":"CorporateTravelDC","original_source_status":"local","version_controlled_author":"CorporateTravelDC","source_kind":"local","needs":"read-only"}
    if defect=="new-skill": (r/"skills/new/SKILL.md").parent.mkdir();(r/"skills/new/SKILL.md").write_text("new")
    if defect=="missing-needs": (r/"skills/skill-capabilities.txt").write_text("")
    if defect in ("modified-upstream","extra-upstream"):
        import hashlib
        (package/"upstream").mkdir();(package/"upstream/SKILL.md").write_text("safe")
        digest=hashlib.sha256(b"safe").hexdigest()
        meta.update(upstream_files=[{"path":"SKILL.md","sha256":digest,"bytes":4}],upstream_tree_sha256=hashlib.sha256(("SKILL.md\0"+digest+"\n").encode()).hexdigest())
        if defect=="modified-upstream": (package/"upstream/SKILL.md").write_text("changed")
        else: (package/"upstream/extra.txt").write_text("new")
    if defect=="divergent-mirror":
        twin=r/".agents/skills/example";twin.mkdir(parents=True);(twin/"SKILL.md").write_text("different")
    if defect!="missing-provenance": (package/"PROVENANCE.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError):catalog.build(r)

@pytest.fixture
def all_sources(tmp_path,monkeypatch):
    import hashlib,sys,types
    r=tmp_path/"all-repo"
    shutil.copytree(REPO/"skills",r/"skills")
    manifest="".join(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+str(p.relative_to(r))+"\n" for p in sorted((r/"skills").rglob("*")) if p.is_file())
    (r/"MANIFEST.sha256").write_text(manifest)
    grants=tmp_path/"grants";grants.write_text("grant fixture-reader *\n")
    sg=catalog.grants_module(REPO)
    monkeypatch.setattr(route,"SKILLS_REPO",str(r));monkeypatch.setattr(route,"SKILL_GRANTS_CONF",str(grants))
    monkeypatch.setattr(route,"_SKILL_GRANTS_MOD",sg)
    wm=types.ModuleType("web.main");wm._scrub_gate=lambda *a,**k:None
    import web
    monkeypatch.setitem(sys.modules,"web.main",wm);monkeypatch.setattr(web,"main",wm,raising=False)
    return r,grants

def test_every_current_package_is_readable_through_same_gateway(all_sources):
    metadata=route._skill_read("*","fixture-reader","*")
    expected={e["name"] for e in catalog.build(REPO)["skills"]}
    assert {e["name"] for e in metadata}==expected
    for name in expected:
        assert route._skill_read(name,"fixture-reader").strip()
        assert "PROVENANCE.json" in route._skill_read(name,"fixture-reader","*")
    assert route._skill_read("*","fixture-denied","*")==[]
