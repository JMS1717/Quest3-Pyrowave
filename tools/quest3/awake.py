"""Keep Quest 3 awake temporarily, then restore physical proximity control."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from .bench import adb_run

def restore(path):
    state=json.loads(path.read_text())
    if state['status']=='restored':return
    # Pin rollback to the same headset; never act on another attached Android device.
    result=subprocess.run([state['adb'],'-s',state['device'],'shell','am','broadcast','-a',
        'com.oculus.vrpowermanager.automation_disable'],capture_output=True,text=True,timeout=20)
    state.update({'status':'restored' if result.returncode==0 else 'restore_failed',
        'restore_result':result.stdout+result.stderr,'restored_at_utc':datetime.now(timezone.utc).isoformat()})
    path.write_text(json.dumps(state,indent=2))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb',default='adb');parser.add_argument('--minutes',type=int,default=30)
    parser.add_argument('--state',required=True);parser.add_argument('--restore',action='store_true')
    parser.add_argument('--timer-worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args();path=Path(args.state).resolve()
    if args.restore:restore(path);print('Physical proximity control restored');return
    if args.timer_worker:
        state=json.loads(path.read_text())
        time.sleep(max(0,state['restore_at_epoch_s']-time.time()))
        restore(path);return
    if not 1<=args.minutes<=120:parser.error('Use 1–120 minutes')
    if adb_run(args.adb,'shell','getprop','ro.product.model').strip()!='Quest 3':
        raise ValueError('Expected Quest 3')
    state={'adb':str(Path(args.adb).resolve()) if Path(args.adb).is_file() else args.adb,
        'device':adb_run(args.adb,'get-serialno').strip(), 'status':'pending',
        'restore_at_epoch_s':time.time()+args.minutes*60,
        'method':'prox_close broadcast; restore with automation_disable; no persistent power settings'}
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as out:json.dump(state,out,indent=2)
    adb_run(args.adb,'shell',f'am broadcast -a com.oculus.vrpowermanager.prox_close --ei duration {args.minutes*60000}')
    state['status']='active';path.write_text(json.dumps(state,indent=2))
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
    worker=subprocess.Popen([sys.executable,'-m','tools.quest3.awake','--state',str(path),'--timer-worker'],
        cwd=Path(__file__).resolve().parents[2],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**options)
    state['restore_worker_pid']=worker.pid;path.write_text(json.dumps(state,indent=2))
    print(json.dumps({'status':'active','restore_at_utc':datetime.fromtimestamp(state['restore_at_epoch_s'],timezone.utc).isoformat(),
        'minutes':args.minutes,'state':str(path)}))

if __name__=='__main__':main()
