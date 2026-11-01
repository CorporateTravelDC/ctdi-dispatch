"""Public derivatives retain the independent privacy gate and private source pins."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import subprocess
import xml.etree.ElementTree as ET
import pytest

REPO=Path(__file__).resolve().parents[2]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,REPO/path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

scrub=load('vendor_derivative_scrub','scripts/scrub-public-tree.py')
catalog=load('vendor_derivative_catalog','scripts/skills-catalog.py')

def test_independent_verifier_is_unchanged_from_baseline():
    old=subprocess.check_output(['git','-C',str(REPO),'show','HEAD:scripts/scrub-public-tree.py'],text=True)
    new=(REPO/'scripts/scrub-public-tree.py').read_text()
    def verifier(source):
        return next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='verify_scrubbed')
    assert ast.dump(verifier(old))==ast.dump(verifier(new))

@pytest.mark.parametrize('path',sorted(scrub._UPSTREAM_SVG_PATHS))
def test_svg_token_stream_and_all_other_attributes_survive(path):
    before=(REPO/path).read_bytes()
    after=scrub.public_blob_bytes(before,Path(path).name,path)
    left=list(ET.fromstring(before).iter());right=list(ET.fromstring(after).iter())
    assert len(left)==len(right)
    for a,b in zip(left,right):
        assert a.tag==b.tag
        assert {k:v for k,v in a.attrib.items() if k!='d'}=={k:v for k,v in b.attrib.items() if k!='d'}
        if 'd' in a.attrib:
            assert scrub._SVG_TOKEN.findall(a.attrib['d'].encode())==scrub._SVG_TOKEN.findall(b.attrib['d'].encode())
    assert not scrub.IPV4_RE.search(after)

def test_malformed_svg_path_is_not_hidden():
    before=b'<svg><path d="M1 2 malformed@example.test"/></svg>'
    assert scrub._normalize_svg_paths(before)==before

def test_contacts_are_redacted_only_in_reviewed_upstream_files(monkeypatch):
    # Exercise the unlisted-contact branch with a reserved, synthetic address.
    monkeypatch.setattr(scrub,'ALLOWED_EMAIL_DOMAIN_SUFFIXES',(b'.invalid',))
    data=b'"email": "author@'+b'vendor.test"'
    path='skills/example/upstream/plugin.json'
    monkeypatch.setattr(scrub,'_UPSTREAM_CONTACT_PATHS',{path})
    assert b'upstream@example.org' in scrub.public_blob_bytes(data,'plugin.json',path)
    assert scrub.public_blob_bytes(data,'plugin.json','config/unreviewed.json')==data

def package(tmp_path):
    root=tmp_path/'fixture';(root/'upstream').mkdir(parents=True)
    rows=[];private=hashlib.sha256();public=hashlib.sha256()
    for path,data in [('SKILL.md',b'fixture instruction'),('asset.png',b'fixture binary')]:
        (root/'upstream'/path).write_bytes(data)
        digest=hashlib.sha256(data).hexdigest();rows.append(dict(path=path,sha256=digest,bytes=len(data)))
        private.update((path+'\0'+digest+'\n').encode())
        if path=='SKILL.md':public.update((path+'\0'+digest+'\n').encode())
    meta=dict(upstream_files=rows,upstream_tree_sha256=private.hexdigest(),public_tree_sha256=public.hexdigest(),public_omitted_files=['asset.png'])
    return root,meta

def test_private_inventory_remains_complete(tmp_path):
    root,meta=package(tmp_path)
    catalog.verify_upstream(root,meta)
    (root/'upstream/asset.png').unlink()
    with pytest.raises(ValueError,match='incomplete'):
        catalog.verify_upstream(root,meta)

def test_public_inventory_requires_exact_declared_binary_omissions(tmp_path):
    root,meta=package(tmp_path)
    (root/'upstream/asset.png').unlink()
    catalog.verify_upstream(root,meta,True)
    (root/'upstream/SKILL.md').unlink()
    with pytest.raises(ValueError,match='incomplete'):
        catalog.verify_upstream(root,meta,True)

def test_public_kept_bytes_and_inventory_hash_still_must_match(tmp_path):
    root,meta=package(tmp_path);(root/'upstream/asset.png').unlink()
    (root/'upstream/SKILL.md').write_bytes(b'altered')
    with pytest.raises(ValueError,match='bytes differ'):
        catalog.verify_upstream(root,meta,True)

def test_public_cannot_omit_text_or_hide_extra_files(tmp_path):
    root,meta=package(tmp_path);meta['public_omitted_files']=['SKILL.md']
    with pytest.raises(ValueError,match='binary omission'):
        catalog.verify_upstream(root,meta,True)
    meta['public_omitted_files']=['asset.png'];(root/'upstream/asset.png').unlink()
    (root/'upstream/extra.txt').write_text('extra')
    with pytest.raises(ValueError,match='unexpected'):
        catalog.verify_upstream(root,meta,True)
