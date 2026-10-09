from pathlib import Path
import json,sys
root=Path(__file__).resolve().parent
schema=json.loads((root/'schemas/profile.schema.json').read_text())
errors=[]
files=sorted((root/'profiles').glob('*.json'))
for p in files:
    d=json.loads(p.read_text())
    if set(d)!=set(schema['properties']):errors.append(f'{p.name}: top-level keys')
    if p.stem!='base' and d['deployment']['profile']!=p.stem:errors.append(f'{p.name}: profile id')
    if not d['security']['default_deny'] or d['security']['network_exposure']!='private_overlay':errors.append(f'{p.name}: unsafe security defaults')
    try:
        import jsonschema
        jsonschema.validate(d,schema)
    except ImportError:pass
    except Exception as e:errors.append(f'{p.name}: {e}')
print(f'Checked {len(files)} profiles')
if errors:
    print(*errors,sep='\n');sys.exit(1)
print('PASS: schema and security invariants')
