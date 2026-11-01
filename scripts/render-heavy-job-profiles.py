#!/usr/bin/env python3
"""Render/check common heavy-job drop-ins from the explicit member registry."""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def expected(config):
    result={}
    for unit,entry in config['members'].items():
        if entry['kind']=='container':
            rel=Path('.config/containers/systemd')/(unit.removesuffix('.service')+'.container.d')/'90-heavy-job-policy.conf'
            container='[Container]\nMemory=2560m\nPodmanArgs=\nPodmanArgs=--memory-swap=6656m\n\n'
        else:
            rel=Path('.config/systemd/user')/(unit+'.d')/'90-heavy-job-policy.conf';container=''
        result[rel]=('# Shared heavy-job policy; generated from config/heavy-job-policy.json.\n'+container+
            '[Unit]\nWants=corporatetraveldc-heavy-job-manager.service\nAfter=corporatetraveldc-heavy-job-manager.service\n'
            'OnFailure=corporatetraveldc-unit-failure-notify@%n.service\n\n[Service]\nSlice=production-brain.slice\n'
            'MemoryHigh=2G\nMemoryMax=2560M\nMemorySwapMax=4G\n'
            '# Queue admission is a condition: it never extends the workload timeout.\n'
            'ExecCondition=/usr/bin/python3 -B /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/heavy-job-policy.py admit %n\n'
            'ExecStopPost=/usr/bin/python3 -B /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/heavy-job-policy.py release %n\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args();config=json.loads((ROOT/'config/heavy-job-policy.json').read_text());bad=[]
    for rel,body in expected(config).items():
        target=ROOT/rel
        if args.write:
            target.parent.mkdir(parents=True,exist_ok=True);target.write_text(body)
        elif not target.exists() or target.read_text()!=body:bad.append(str(rel))
    print(json.dumps({'profiles':len(config['members']),'ok':not bad,'drift':bad}))
    return 1 if bad else 0


if __name__=='__main__':raise SystemExit(main())
