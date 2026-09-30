"""Explicit, reversible Quest 3 developer display-scaling experiment."""
import argparse
import json
from pathlib import Path
from .bench import adb_run

PROPERTIES = ('debug.oculus.forceDisplayScaling', 'debug.oculus.refreshRate')

def values(adb):
    return {name:adb_run(adb,'shell','getprop',name).strip() for name in PROPERTIES}

def set_property(adb,name,value):
    # Only fixed property names and validated numeric/empty values reach the remote shell.
    if name not in PROPERTIES or (value and not value.isdigit()):
        raise ValueError('Unexpected property/value; refusing remote shell command')
    adb_run(adb,'shell',f"setprop {name} '{value}'")
    if adb_run(adb,'shell','getprop',name).strip()!=value:
        raise RuntimeError(f'Property write rejected: {name}')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb',default='adb')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('status')
    for action in ('enable','restore'):
        sub.add_parser(action).add_argument('--state',required=True)
    args=parser.parse_args()
    model=adb_run(args.adb,'shell','getprop','ro.product.model').strip()
    if model!='Quest 3':raise RuntimeError(f'Expected Quest 3, found {model}')
    device=adb_run(args.adb,'get-serialno').strip()
    if args.command=='status':print(json.dumps({'model':model,'properties':values(args.adb)}));return
    state=Path(args.state)
    if args.command=='enable':
        original={'model':model,'device':device,'properties':values(args.adb)}
        # Exclusive create: never overwrite the only rollback state.
        state.parent.mkdir(parents=True,exist_ok=True)
        with state.open('x',encoding='utf-8') as out:json.dump(original,out,indent=2)
        try:
            set_property(args.adb,PROPERTIES[0],'1')
            set_property(args.adb,PROPERTIES[1],'240')
        except Exception:
            for name,value in original['properties'].items():set_property(args.adb,name,value)
            raise
    else:
        original=json.loads(state.read_text())
        if original.get('device')!=device or set(original['properties'])!=set(PROPERTIES):
            raise ValueError('Rollback state belongs to a different device or property set')
        for name,value in original['properties'].items():set_property(args.adb,name,value)
    print(json.dumps({'model':model,'properties':values(args.adb),'action':args.command}))

if __name__=='__main__':main()
