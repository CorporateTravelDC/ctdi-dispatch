#!/usr/bin/env python3
"""Operator-only install of reviewed, signed heavy-job profiles; optional pilot."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PRODUCTION=Path('/opt/corporatetraveldc/private/ctdi-dispatch-internal')
UNITS=('production-brain.slice','corporatetraveldc-heavy-job-manager.service',
       'corporatetraveldc-brain-capacity-review.service','corporatetraveldc-brain-capacity-review.timer')


def run(args,timeout=30):
    r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError('installation check failed; inspect operator logs')
    return r.stdout.strip()


def argument_check(base):
    # Resetting the PodmanArgs list is safe only when it contains exactly
    # the one existing swap flag. Refuse unrelated local customization.
    values=[]
    for path in [base,*sorted(base.with_name(base.name+'.d').glob('*.conf'))]:
        for line in path.read_text().splitlines():
            if line.startswith('PodmanArgs='):
                value=line.partition('=')[2].strip()
                if not value:values=[]
                else:values.append(value)
    if len(values)!=1 or not re.fullmatch(r'--memory-swap=[0-9]+[mMgG]?',values[0]):
        raise RuntimeError('local PodmanArgs need review; no profiles installed')


def planned(root,home,p):
    pairs=[]
    for name in UNITS:
        rel=Path('.config/systemd/user')/name;pairs.append((root/rel,home/rel))
    for unit,entry in p['members'].items():
        rel=(Path('.config/containers/systemd')/(unit.removesuffix('.service')+'.container.d')/'90-heavy-job-policy.conf'
             if entry['kind']=='container' else Path('.config/systemd/user')/(unit+'.d')/'90-heavy-job-policy.conf')
        if entry['kind']=='container':argument_check(home/rel.parent.with_name(rel.parent.name.removesuffix('.d')))
        pairs.append((root/rel,home/rel))
    # This installer owns only identical profiles or exact known v4 files.
    allowed=p.get('legacy_hashes',{})
    for source,dest in pairs:
        if dest.is_symlink():raise RuntimeError('local configuration symlink needs review')
        if dest.exists() and dest.read_bytes()!=source.read_bytes():
            if hashlib.sha256(dest.read_bytes()).hexdigest() not in allowed.get(str(dest.relative_to(home)),[]):
                raise RuntimeError('existing local policy differs; no files replaced')
    retired=[]
    for rel,hashes in allowed.items():
        dest=home/rel
        if any(dest==target for _,target in pairs):continue
        if dest.is_symlink():raise RuntimeError('legacy policy symlink needs review')
        if dest.exists():
            if hashlib.sha256(dest.read_bytes()).hexdigest() not in hashes:
                raise RuntimeError('legacy policy differs; review before migration')
            retired.append(dest)
    return pairs,retired


def install(p,home):
    names=set(p['members'])|{'corporatetraveldc-brain-swap-guard.service'}
    raw=run(['systemctl','--user','show',*sorted(names),'-p','Id,ActiveState'])
    # No running job is stopped or restarted. A timer race after this check
    # is caught by admission as untracked work; it cannot admit a third job.
    if any(line in ('ActiveState=active','ActiveState=activating','ActiveState=deactivating') for line in raw.splitlines()):
        raise RuntimeError('wait for current jobs to finish before installing profiles')
    pairs,retired=planned(ROOT,home,p)
    backup=Path(tempfile.mkdtemp(prefix='ctdi-heavy-policy-backup-'))
    for path in retired:
        relative=path.relative_to(home);dest=backup/relative;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,dest);dest.chmod(0o600)
    for source,dest in pairs:
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists() and dest.read_bytes()!=source.read_bytes():
            saved=backup/dest.relative_to(home);saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(dest,saved);saved.chmod(0o600)
        fd,temp=tempfile.mkstemp(prefix='.heavy-policy-',dir=dest.parent)
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o644);f.write(source.read_bytes());f.flush();os.fsync(f.fileno())
        os.replace(temp,dest)
    for path in retired:path.unlink()
    run(['systemctl','--user','daemon-reload'])
    run(['systemctl','--user','enable','--now','corporatetraveldc-heavy-job-manager.service'])
    run(['systemctl','--user','enable','--now','corporatetraveldc-brain-capacity-review.timer'])
    print(json.dumps({'installed':True,'members':len(p['members']),'max_parallel':2,
                      'member_timers_changed':False,'backup':str(backup)}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot',action='store_true',help='observe semantic compile and RSS through the shared queue')
    args=parser.parse_args()
    if os.getuid()==0 or pwd.getpwuid(os.getuid()).pw_name!='corporatetraveldc' or ROOT!=PRODUCTION:
        print('Run from the operator account in the reviewed signed production checkout.');return 77
    try:
        run(['bash',str(ROOT/'scripts/verify-manifest.sh')],timeout=180)
        run(['/usr/bin/python3','-B',str(ROOT/'scripts/render-heavy-job-profiles.py'),'--check'])
        spec=importlib.util.spec_from_file_location('signed_heavy_policy',ROOT/'scripts/heavy-job-policy.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);p=m.policy()
        install(p,Path.home())
        if args.pilot:
            receipt=Path('/srv/ctdc-codex')/('heavy-policy-pilot-'+m.datetime.now(m.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
            result=m.pilot(['corporatetraveldc-semantic-compile-daily.service','corporatetraveldc-second-brain-rss.service'],p,receipt)
            if result:return result
            # The review also reports unrelated existing failed jobs faithfully.
            run(['systemctl','--user','start','corporatetraveldc-brain-capacity-review.service'],timeout=200)
        return 0
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError):
        print('Installation or pilot needs attention; inspect operator logs. No running workload was stopped.')
        return 1


if __name__=='__main__':raise SystemExit(main())
