#!/usr/bin/env python3
"""Validate the shared source catalog without executing skills or accessing live state."""
from pathlib import Path, PurePosixPath
import argparse, hashlib, importlib.util, json

def grants_module(repo):
    spec=importlib.util.spec_from_file_location("catalog_grants",repo/"scripts/lib/skill_grants.py")
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def verify_upstream(root, meta, public_edition=False):
    """Require complete private source; account explicitly for public binary drops.

    The signed public publisher carries PRIVATE-TREE-MANIFEST.sha256. Only that
    edition may omit the binary paths named in provenance; remaining bytes and
    the exact complete public inventory still require their recorded hashes.
    """
    sg=grants_module(Path(__file__).resolve().parents[1])
    actual=sg.tree_files(str(root/"upstream"))
    expected=[];h=hashlib.sha256();public=False
    omitted=meta.get("public_omitted_files",[])
    if not isinstance(omitted,list) or len(set(omitted))!=len(omitted):
        raise ValueError('invalid public omission inventory')
    for rel in omitted:
        p=PurePosixPath(rel)
        if p.is_absolute() or '..' in p.parts or str(p)!=rel or p.suffix.lower() not in {'.png','.jpg','.jpeg','.gif','.ico','.pdf','.woff','.woff2','.ttf','.zip','.tar','.gz'}:
            raise ValueError('invalid public binary omission')
    missing=[]
    for row in meta['upstream_files']:
        rel=row['path'];p=PurePosixPath(rel)
        if p.is_absolute() or '..' in p.parts or str(p)!=rel:
            raise ValueError('invalid upstream path')
        expected.append(rel)
        path=root/'upstream'/rel
        if not path.is_file():
            missing.append(rel)
            continue
        data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
        if digest!=row['sha256'] or len(data)!=row['bytes']:
            if digest!=row.get('public_sha256') or len(data)!=row.get('public_bytes'):
                raise ValueError('upstream bytes differ')
            public=True
        h.update((rel+'\0'+digest+'\n').encode())
    if len(set(expected))!=len(expected) or not set(omitted)<=set(expected):
        raise ValueError('invalid upstream inventory')
    if missing:
        if not public_edition or set(missing)!=set(omitted):
            raise ValueError('incomplete upstream file inventory')
        public=True
    elif public_edition and omitted:
        raise ValueError('public inventory contains assets declared omitted')
    if sorted(set(expected)-set(missing))!=[r for r,_ in actual]:
        raise ValueError('unexpected upstream file inventory')
    key='public_tree_sha256' if public else 'upstream_tree_sha256'
    if h.hexdigest()!=meta[key]:
        raise ValueError('upstream tree differs')

def build(repo):
    repo=Path(repo);sg=grants_module(repo)
    attestation=repo/'PRIVATE-TREE-MANIFEST.sha256'
    public_edition=attestation.is_file() and not attestation.is_symlink()
    cat=sg.catalog(str(repo),"/nonexistent")
    needs=sg.read_needs(str(repo));out=[]
    for name,entry in sorted(cat.items()):
        if entry["source"]!="signed":
            raise ValueError(f"{name}: not in canonical signed catalog")
        root=Path(entry["path"]);sg.tree_files(str(root))
        if not (root/"SKILL.md").is_file():
            raise ValueError(f"{name}: missing SKILL.md")
        try: meta=json.loads((root/"PROVENANCE.json").read_text())
        except (OSError,ValueError) as e: raise ValueError(f"{name}: missing or invalid provenance") from e
        for key in ("name","original_source","original_source_status","version_controlled_author","source_kind","needs"):
            if not isinstance(meta.get(key),str) or not meta[key].strip():
                raise ValueError(f"{name}: missing {key}")
        if meta["name"]!=name or meta["version_controlled_author"]!="CorporateTravelDC":
            raise ValueError(f"{name}: inconsistent identity")
        if not needs.get(name) or meta["needs"]!=needs[name]:
            raise ValueError(f"{name}: missing or inconsistent needs")
        if "upstream_files" in meta:
            verify_upstream(root,meta,public_edition)
        for rel in (".claude/skills",".agents/skills"):
            twin=repo/rel/name
            if twin.is_dir() and sg.tree_hash(str(twin))!=sg.tree_hash(str(root)):
                # Existing project discovery mirrors may predate metadata.
                # Their instruction/helper bytes must still agree; metadata is canonical.
                for sub,p in sg.tree_files(str(twin)):
                    target=root/sub
                    if not target.is_file() or target.read_bytes()!=Path(p).read_bytes():
                        raise ValueError(f"{name}: divergent discovery mirror")
        out.append({key:meta[key] for key in ("name","original_source","original_source_status","version_controlled_author","source_kind","needs")})
    for base in (repo/".agents/skills",repo/".claude/skills"):
        if base.is_dir():
            for skill in base.iterdir():
                if skill.is_dir() and (skill/"SKILL.md").is_file() and skill.name not in cat:
                    raise ValueError(f"{skill.name}: discovery-only skill has no canonical package")
    return {"schema_version":1,"version_controlled_author":"CorporateTravelDC",
            "gateway_tool":"skill_read","skills":out}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument("--check",action="store_true",help="validate the generated catalog (the default)")
    ap.add_argument("--write",action="store_true",help="write review index in your authorized checkout")
    args=ap.parse_args()
    try:
        result=build(args.repo);p=args.repo/"skills/catalog.json"
        if args.write: p.write_text(json.dumps(result,indent=2)+"\n")
        elif json.loads(p.read_text())!=result: raise ValueError("catalog index is stale; regenerate and review")
        print(f"PASS: {len(result['skills'])} canonical packages; provenance, needs, upstream bytes and mirrors checked")
        return 0
    except (OSError,ValueError,KeyError,TypeError) as e:
        print(f"FAIL: {e}");return 1
if __name__=="__main__": raise SystemExit(main())
