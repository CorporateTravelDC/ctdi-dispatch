"""Granted MCP package reads against isolated files; no signing or live auth."""
import hashlib,importlib.util,json,sys,types
from pathlib import Path
import pytest
from web.routes import agent_gateway as route

@pytest.fixture
def package(tmp_path,monkeypatch):
    r=tmp_path/"repo";p=r/"skills/alpha";p.mkdir(parents=True)
    for rel,content in {"SKILL.md":b"# Synthetic instructions\n","references/guide.md":b"Safe reference\n","scripts/helper.py":b"raise SystemExit(99)\n","assets/icon.png":b"\x89PNG\r\n\x1a\n\xff"}.items():
        f=p/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(content)
    (p/"PROVENANCE.json").write_text(json.dumps({"name":"alpha","original_source":"CorporateTravelDC","original_source_status":"fixture","version_controlled_author":"CorporateTravelDC","source_kind":"fixture","needs":"read only"}))
    (r/"skills/beta").mkdir();(r/"skills/beta/SKILL.md").write_text("beta")
    def sign_fixture():
        (r/"MANIFEST.sha256").write_text("".join(hashlib.sha256(f.read_bytes()).hexdigest()+"  "+str(f.relative_to(r))+"\n" for f in sorted((r/"skills").rglob("*")) if f.is_file()))
    sign_fixture();grants=tmp_path/"grants";grants.write_text("grant bot alpha\n")
    spec=importlib.util.spec_from_file_location("package_grants",Path(__file__).resolve().parents[2]/"scripts/lib/skill_grants.py")
    sg=importlib.util.module_from_spec(spec);spec.loader.exec_module(sg)
    monkeypatch.setattr(route,"_SKILL_GRANTS_MOD",sg);monkeypatch.setattr(route,"SKILLS_REPO",str(r));monkeypatch.setattr(route,"SKILL_GRANTS_CONF",str(grants))
    calls=[];wm=types.ModuleType("web.main");wm._scrub_gate=lambda text,source:calls.append((text,source))
    import web
    monkeypatch.setitem(sys.modules,"web.main",wm);monkeypatch.setattr(web,"main",wm,raising=False)
    return r,p,grants,sg,sign_fixture,calls

def test_default_read_and_reference_and_helper_source(package):
    *_,calls=package
    assert route._skill_read("alpha","bot").startswith("# Synthetic")
    assert route._skill_read("alpha","bot","references/guide.md")=="Safe reference\n"
    assert route._skill_read("alpha","bot","scripts/helper.py")=="raise SystemExit(99)\n"
    assert len(calls)==3

def test_package_listing_requires_grant_and_verifies_files(package):
    assert route._skill_read(None,"bot")==["alpha"]
    assert "assets/icon.png" in route._skill_read("alpha","bot","*")
    for path in ["*","SKILL.md","references/guide.md"]:
        with pytest.raises(ValueError,match="skill not granted"):route._skill_read("beta","bot",path)

@pytest.mark.parametrize("path",["../beta/SKILL.md","/etc/passwd","scripts/../../SKILL.md","./SKILL.md","references//guide.md","references\\guide.md","%2e%2e/x","x:stream","a\x00b","",None])
def test_traversal_and_invalid_paths(package,path):
    with pytest.raises(ValueError,match="invalid skill path"):route._skill_read("alpha","bot",path)

def test_denial_and_expiry_apply_to_helpers(package):
    _,_,g,_,_,_=package
    g.write_text("grant bot alpha\ndeny bot alpha\n")
    with pytest.raises(ValueError,match="skill not granted"):route._skill_read("alpha","bot","scripts/helper.py")
    g.write_text("grant bot alpha until=2000-01-01T00:00:00Z\n")
    with pytest.raises(ValueError,match="skill not granted"):route._skill_read("alpha","bot","*")

def test_tampering_any_file_refuses_whole_package(package):
    _,p,*_=package;(p/"scripts/helper.py").write_text("changed")
    with pytest.raises(ValueError,match="manifest verification"):route._skill_read("alpha","bot","references/guide.md")
    with pytest.raises(ValueError,match="manifest verification"):route._skill_read("alpha","bot","*")

def test_verification_read_replacement_refused(package,monkeypatch):
    _,p,_,sg,_,_=package;verify=sg.verify_source
    def changed(*a):
        result=verify(*a);(p/"references/guide.md").write_text("replaced");return result
    monkeypatch.setattr(sg,"verify_source",changed)
    with pytest.raises(ValueError,match="manifest verification"):route._skill_read("alpha","bot","references/guide.md")

def test_symlinks_refused(package,tmp_path):
    _,p,*_=package;target=tmp_path/"outside";target.write_text("outside")
    (p/"references/guide.md").unlink();(p/"references/guide.md").symlink_to(target)
    with pytest.raises(ValueError,match="manifest verification"):route._skill_read("alpha","bot","references/guide.md")

def test_binary_and_oversized_and_missing_are_not_returned(package):
    _,p,_,_,sign,_=package
    with pytest.raises(ValueError,match="file unavailable"):route._skill_read("alpha","bot","assets/icon.png")
    with pytest.raises(ValueError,match="file unavailable"):route._skill_read("alpha","bot","missing.txt")
    (p/"references/guide.md").write_text("x"*((1<<20)+1));sign()
    with pytest.raises(ValueError,match="file unavailable"):route._skill_read("alpha","bot","references/guide.md")

def test_scrub_block_never_returns_text(package,monkeypatch):
    import web
    def block(*a,**k):raise ValueError("synthetic scrub block")
    monkeypatch.setattr(web.main,"_scrub_gate",block)
    with pytest.raises(ValueError,match="synthetic scrub block"):route._skill_read("alpha","bot")

def test_tool_schema_keeps_existing_name_and_optional_path():
    tool=next(t for t in route.TOOLS if t["name"]=="skill_read")
    assert tool["annotations"]["readOnlyHint"]
    assert "path" in tool["inputSchema"]["properties"]


def test_mcp_dispatch_passes_supporting_path(package):
    assert route._call("skill_read",{"name":"alpha","path":"references/guide.md"},{"account":"bot"})=="Safe reference\n"


@pytest.mark.parametrize("account",["claude-fixture","codex-fixture","cloud-fixture"])
def test_one_catalog_for_different_identities(package,account):
    _,_,g,_,_,_=package
    g.write_text("grant "+account+" alpha\n")
    metadata=route._skill_read("*",account,"*")
    assert [m["name"] for m in metadata]==["alpha"]
    assert metadata[0]["needs"]=="read only"
    assert route._skill_read("*","ungranted-fixture","*")==[]

def test_catalog_metadata_is_verified_and_denial_filtered(package):
    _,p,g,_,sign,_=package
    (p/"PROVENANCE.json").write_text("changed")
    with pytest.raises(ValueError,match="manifest verification"):route._skill_read("*","bot","*")
    g.write_text("grant bot alpha\ndeny bot alpha\n")
    assert route._skill_read("*","bot","*")==[]
