#!/usr/bin/env python3
"""Record the existing public scrub's derived hashes; never edit vendor source."""
from pathlib import Path
import hashlib
import importlib.util
import json


def refresh(repo):
    spec=importlib.util.spec_from_file_location('public_provenance_scrub',repo/'scripts/scrub-public-tree.py')
    scrub=importlib.util.module_from_spec(spec);spec.loader.exec_module(scrub)
    count=0
    for path in sorted((repo/'skills').glob('*/PROVENANCE.json')):
        meta=json.loads(path.read_text())
        if 'upstream_files' not in meta:
            continue
        private=hashlib.sha256();public=hashlib.sha256();omitted=[]
        for row in meta['upstream_files']:
            rel=row['path'];source=path.parent/'upstream'/rel
            data=source.read_bytes();digest=hashlib.sha256(data).hexdigest()
            if digest!=row['sha256'] or len(data)!=row['bytes']:
                raise ValueError('Original upstream bytes changed: '+str(source.relative_to(repo)))
            private.update((rel+'\0'+digest+'\n').encode())
            row.pop('public_sha256',None);row.pop('public_bytes',None)
            if source.suffix.lower() in scrub.BINARY_SKIP_EXTENSIONS and source.name not in scrub.REVIEWED_BINARY_OK:
                omitted.append(rel)
                continue
            derived=scrub.public_blob_bytes(data,source.name,str(source.relative_to(repo)))
            new_digest=hashlib.sha256(derived).hexdigest()
            if derived!=data:
                row.update(public_sha256=new_digest,public_bytes=len(derived))
            public.update((rel+'\0'+new_digest+'\n').encode())
        if private.hexdigest()!=meta['upstream_tree_sha256']:
            raise ValueError('Original upstream tree changed')
        meta['public_tree_sha256']=public.hexdigest()
        meta['public_omitted_files']=omitted
        meta['public_scrub_note']='Original vendor bytes and hashes remain complete privately. Public derivatives use the full privacy scrub; explicitly listed binary omissions follow the existing binary-drop policy. Public hashes cover the exact remaining upstream inventory.'
        path.write_text(json.dumps(meta,indent=2)+'\n')
        for prefix in ('.agents/skills','.claude/skills'):
            twin=repo/prefix/path.parent.name/'PROVENANCE.json'
            if twin.is_file():twin.write_bytes(path.read_bytes())
        count+=1
    index=repo/'skills/source-provenance.json'
    data=json.loads(index.read_text())
    for item in data['openai_imports']:
        item['public_tree_sha256']=json.loads((repo/item['provenance_file']).read_text())['public_tree_sha256']
    index.write_text(json.dumps(data,indent=2)+'\n')
    print('Updated public provenance for',count,'packages; original upstream bytes verified unchanged')


if __name__=='__main__':
    refresh(Path(__file__).resolve().parents[1])
