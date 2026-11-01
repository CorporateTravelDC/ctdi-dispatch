"""Original Codex incompatibility and generalized guardian isolation."""
import importlib.util,json,sys,os,stat
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pytest
REPO=Path(__file__).resolve().parents[2]
SCRIPTS=REPO/"skills/dispatch-context-guardian/scripts"

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

@pytest.fixture
def g(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS));core=load(SCRIPTS/"guardian_core.py","guardian_core");monkeypatch.setitem(sys.modules,"guardian_core",core)
    hook=load(SCRIPTS/"context_hook.py","portable_context_hook");return core,hook

def synthetic_fetch(path):
    if path=="/healthz":return {"status":"ok","unused_private":"SYNTHETIC-OMIT"}
    if path.endswith("feeds"):return {"feeds":[{"age_seconds":99,"stale_threshold_seconds":10,"name":"SYNTHETIC-OMIT"},{"optional":True,"error":"SYNTHETIC-OMIT"}]}
    return {"score":3,"label":"GREEN","other":"SYNTHETIC-OMIT"}

def test_original_as_is_native_codex_precompact_does_not_save(tmp_path,monkeypatch,capsys):
    # Load the original tracked Stop hook unchanged into an isolated namespace.
    import subprocess,io
    src=__import__("subprocess").check_output(["git","-C",str(REPO),"show","HEAD:.claude/skills/dispatch-context-guardian/scripts/context_hook.py"],text=True)
    ns={"__name__":"original_guardian_fixture","__file__":str(tmp_path/"context_hook.py")};exec(compile(src,"original_context_hook.py","exec"),ns)
    called=[];monkeypatch.setattr(subprocess,"run",lambda *a,**k:called.append(a))
    monkeypatch.setattr(sys,"stdin",io.StringIO(json.dumps({"hook_event_name":"PreCompact","session_id":"synthetic-session","transcript_path":None,"trigger":"auto"})))
    ns["LOG_FILE"]=str(tmp_path/"original.log")
    with pytest.raises(SystemExit) as e:ns["main"]()
    assert e.value.code==0 and not called and capsys.readouterr().out==""
    assert "tokens=0" in (tmp_path/"original.log").read_text()

def test_snapshot_reprojects_fields_and_private_modes(g,tmp_path):
    core,_=g;d=core.save("codex","session",tmp_path,synthetic_fetch);p=core.state_path("codex","session",tmp_path)
    assert "SYNTHETIC-OMIT" not in p.read_text()
    assert stat.S_IMODE(p.stat().st_mode)==0o600
    assert stat.S_IMODE(p.parent.stat().st_mode)==0o700
    got=core.restore("codex","session",tmp_path)
    assert got["summary"]["feeds"]=={"available":True,"total":2,"stale_or_error":1}
    assert not got["stale"]

def test_client_and_session_do_not_share_state(g,tmp_path):
    core,_=g;core.save("codex","one",tmp_path,synthetic_fetch)
    for client,session in [("claude","one"),("codex","two")]:
        with pytest.raises(FileNotFoundError):core.restore(client,session,tmp_path)

def test_legacy_raw_and_future_untrusted_fields_are_not_replayed(g,tmp_path):
    core,_=g;core.save("codex","one",tmp_path,synthetic_fetch);p=core.state_path("codex","one",tmp_path)
    d=json.loads(p.read_text());d["summary"]["health"]["status"]="SYNTHETIC-OMIT";d["raw"]="SYNTHETIC-OMIT";p.write_text(json.dumps(d))
    assert "SYNTHETIC-OMIT" not in core.render(core.restore("codex","one",tmp_path))
    d["schema_version"]=1;p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match="unsupported"):core.restore("codex","one",tmp_path)

def test_staleness_and_fetch_failures_are_explicit(g,tmp_path):
    core,_=g;core.save("codex","one",tmp_path,lambda path:None);p=core.state_path("codex","one",tmp_path)
    d=json.loads(p.read_text());assert all(x["available"] is False for x in d["summary"].values())
    d["saved_at"]=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat();p.write_text(json.dumps(d))
    assert core.restore("codex","one",tmp_path)["stale"]

def test_codex_events_save_and_restore_without_transcript_reads(g,tmp_path,monkeypatch):
    core,hook=g;monkeypatch.setattr(hook,"save",lambda c,s,r:core.save(c,s,r,synthetic_fetch))
    saved=hook.handle({"hook_event_name":"PreCompact","session_id":"one","transcript_path":"/forbidden/never-read"},"codex",root=tmp_path)
    assert "saved" in saved["systemMessage"]
    got=hook.handle({"hook_event_name":"SessionStart","session_id":"one","source":"compact"},"codex",root=tmp_path)
    assert "cached" in got["hookSpecificOutput"]["additionalContext"]
    assert got["hookSpecificOutput"]["hookEventName"]=="SessionStart"
    assert "continue" not in got

def test_invalid_and_recursing_events_do_not_operate(g,tmp_path):
    _,hook=g
    before=set(tmp_path.iterdir())
    assert hook.handle({"stop_hook_active":True},"codex",root=tmp_path)=={}
    assert "unavailable" in hook.handle({"hook_event_name":"Stop"},"codex",root=tmp_path)["systemMessage"]
    assert set(tmp_path.iterdir())==before

def test_unsafe_symlink_refused(g,tmp_path):
    core,_=g;p=core.state_path("codex","one",tmp_path);p.parent.mkdir();target=tmp_path/"outside";target.write_text("unread");p.symlink_to(target)
    with pytest.raises(ValueError,match="unsafe"):core.restore("codex","one",tmp_path)

def test_three_discovery_copies_have_identical_sources():
    canon=REPO/"skills/dispatch-context-guardian"
    for rel in [".claude/skills/dispatch-context-guardian",".agents/skills/dispatch-context-guardian"]:
        mirror=REPO/rel
        for p in canon.rglob("*"):
            if p.is_file():assert p.read_bytes()==(mirror/p.relative_to(canon)).read_bytes()


def test_unconfigured_feed_is_idle_not_failing(g):
    core,_=g
    result=core.summarize("feeds",{"feeds":[{"error":"awaiting_credentials","age_seconds":None}]})
    assert result["stale_or_error"]==0
