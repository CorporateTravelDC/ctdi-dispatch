"""Client-neutral, summary-only operational checkpoints. No keys or transcripts."""
from __future__ import annotations
import hashlib,json,math,os,tempfile
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import Request,urlopen

BASE="http://100.x.x.x:8000"
ENDPOINTS={"health":"/healthz","feeds":"/api/v1/feeds","cps":"/api/v1/cps"}
CLIENTS={"codex","claude","chatgpt","cowork","generic"}
MAX_BYTES=262144


def _number(value):
    return value if type(value) in (int,float) and math.isfinite(value) else None


def summarize(kind,data):
    if not isinstance(data,dict):return {"available":False}
    if kind=="health":
        value=data.get("status")
        return {"available":True,"status":value if value in ("ok","healthy","up","degraded","error","unhealthy") else "unknown"}
    if kind=="cps":
        label=data.get("label")
        return {"available":True,"score":_number(data.get("score")),"label":label if label in ("GREEN","YELLOW","RED","green","yellow","red") else "unknown"}
    feeds=data.get("feeds")
    if not isinstance(feeds,list):return {"available":False}
    stale=0
    for f in feeds:
        if not isinstance(f,dict):continue
        age=_number(f.get("age_seconds"));limit=_number(f.get("stale_threshold_seconds"))
        optional=f.get("optional") is True or f.get("push_covered") is True or f.get("error")=="awaiting_credentials"
        if not optional and (bool(f.get("error")) or (age is not None and limit is not None and age>limit)):stale+=1
    return {"available":True,"total":len(feeds),"stale_or_error":stale}


def fetch(path):
    try:
        req=Request(BASE+path,headers={"Accept":"application/json"})
        with urlopen(req,timeout=3) as r:raw=r.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:return None
        return json.loads(raw)
    except Exception:return None


def state_path(client,session,root=None):
    if client not in CLIENTS or not isinstance(session,str) or not session or len(session)>512:
        raise ValueError("invalid checkpoint identity")
    root=Path(root) if root is not None else Path.home()/".local/state/ctdc-context-guardian"
    digest=hashlib.sha256(session.encode()).hexdigest()[:24]
    return root/client/(digest+".json")


def save(client,session,root=None,fetcher=fetch):
    p=state_path(client,session,root)
    data={"schema_version":2,"client":client,"saved_at":datetime.now(timezone.utc).isoformat(),
          "summary":{k:summarize(k,fetcher(v)) for k,v in ENDPOINTS.items()}}
    p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if p.parent.is_symlink() or p.is_symlink():raise ValueError("unsafe checkpoint path")
    os.chmod(p.parent,0o700)
    fd,tmp=tempfile.mkstemp(prefix=".checkpoint-",dir=p.parent)
    try:
        with os.fdopen(fd,"w") as f:
            os.fchmod(f.fileno(),0o600);json.dump(data,f,sort_keys=True);f.write("\n")
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return data


def restore(client,session,root=None):
    p=state_path(client,session,root)
    if p.parent.is_symlink() or p.is_symlink():raise ValueError("unsafe checkpoint path")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,"rb") as f:raw=f.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError("oversized checkpoint")
    data=json.loads(raw)
    if data.get("schema_version")!=2 or data.get("client")!=client:raise ValueError("unsupported checkpoint")
    at=datetime.fromisoformat(data["saved_at"])
    if at.tzinfo is None:raise ValueError("unlabelled checkpoint time")
    age=max(0,int((datetime.now(timezone.utc)-at).total_seconds()))
    # Reproject fixed scalar fields; never replay arbitrary file text into a model.
    summary=data.get("summary",{});out={}
    h=summary.get("health",{});out["health"]=summarize("health",h) if h.get("available") is True else {"available":False}
    c=summary.get("cps",{});out["cps"]=summarize("cps",c) if c.get("available") is True else {"available":False}
    f=summary.get("feeds",{});out["feeds"]={"available":f.get("available") is True,
        "total":_number(f.get("total")),"stale_or_error":_number(f.get("stale_or_error"))}
    return {"client":client,"age_seconds":age,"stale":age>900,"summary":out}


def render(data):
    return "Guardian operational checkpoint (cached; recheck live before decisions): "+json.dumps(data,sort_keys=True)
