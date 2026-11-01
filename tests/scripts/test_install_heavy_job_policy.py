import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(name):
    path=Path(__file__).resolve().parents[2]/'scripts'/name
    spec=importlib.util.spec_from_file_location('fixture_'+name.replace('-','_').replace('.','_'),path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_generated_profiles_have_no_drift():
    m=load('render-heavy-job-profiles.py')
    config=json.loads((m.ROOT/'config/heavy-job-policy.json').read_text())
    assert all((m.ROOT/p).read_text()==body for p,body in m.expected(config).items())


def test_all_current_heavy_families_and_model_wrappers_register_the_default():
    root=Path(__file__).resolve().parents[2];config=json.loads((root/'config/heavy-job-policy.json').read_text())
    exempt={'corporatetraveldc-ops-brief','corporatetraveldc-ep-advance'}
    for path in (root/'.config/containers/systemd').glob('*.container'):
        text=path.read_text();stem=path.stem
        family=any(x in stem for x in ('second-brain','semantic-compile','knowledge-graph','retrofit-links',
                    'entity-tracking','personal-notes-import','personal-export-watch','research-board-mirror','weekly-summary'))
        if family or ('long-runner-unit.lock' in text and stem not in exempt):
            assert stem+'.service' in config['members']


def test_unrelated_podman_arguments_cannot_be_erased(tmp_path):
    m=load('install-heavy-job-policy.py');base=tmp_path/'job.container'
    base.write_text('[Container]\nPodmanArgs=--memory-swap=512m --pids-limit=64\n')
    with pytest.raises(RuntimeError,match='PodmanArgs'):m.argument_check(base)
    base.write_text('[Container]\nPodmanArgs=--memory-swap=512m\n')
    m.argument_check(base)


def test_existing_custom_policy_is_not_overwritten(tmp_path):
    m=load('install-heavy-job-policy.py');root=tmp_path/'source';home=tmp_path/'operator'
    for name in m.UNITS:
        source=root/'.config/systemd/user'/name;source.parent.mkdir(parents=True,exist_ok=True);source.write_text('reviewed source')
    dest=home/'.config/systemd/user'/m.UNITS[0];dest.parent.mkdir(parents=True);dest.write_text('custom operator policy')
    with pytest.raises(RuntimeError,match='differs'):m.planned(root,home,{'members':{}})
    assert dest.read_text()=='custom operator policy'


def test_container_plan_reads_installed_arguments_and_builds_correct_dropin_path(tmp_path):
    m=load('install-heavy-job-policy.py');root=tmp_path/'source';home=tmp_path/'operator'
    for name in m.UNITS:
        f=root/'.config/systemd/user'/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('reviewed source')
    stem='corporatetraveldc-second-brain-rss'
    base=home/'.config/containers/systemd'/(stem+'.container');base.parent.mkdir(parents=True)
    base.write_text('[Container]\nPodmanArgs=--memory-swap=512m\n')
    profile=root/'.config/containers/systemd'/(stem+'.container.d')/'90-heavy-job-policy.conf'
    profile.parent.mkdir(parents=True);profile.write_text('reviewed profile')
    pairs,retired=m.planned(root,home,{'members':{stem+'.service':{'kind':'container'}}})
    assert (profile,home/profile.relative_to(root)) in pairs and not retired


def test_busy_jobs_block_installation_before_any_write(tmp_path,monkeypatch):
    m=load('install-heavy-job-policy.py')
    monkeypatch.setattr(m,'run',lambda args,**kw:'Id=fixture\nActiveState=activating\n')
    before=set(tmp_path.iterdir())
    with pytest.raises(RuntimeError,match='finish'):m.install({'members':{}},tmp_path)
    assert set(tmp_path.iterdir())==before


def test_agent_cannot_install_operator_units(monkeypatch):
    m=load('install-heavy-job-policy.py')
    monkeypatch.setattr(m.os,'getuid',lambda:1003)
    monkeypatch.setattr(m.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_name='ctdc-agent-openai-codex'))
    monkeypatch.setattr(m.argparse.ArgumentParser,'parse_args',lambda self:SimpleNamespace(pilot=False))
    monkeypatch.setattr(m,'run',lambda *a,**kw:pytest.fail('agent installation'))
    assert m.main()==77
