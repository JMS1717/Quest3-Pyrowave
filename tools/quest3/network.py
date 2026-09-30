"""Native UDP frame-burst benchmark, with achieved rate and receiver deadline accounting."""
import argparse
import ipaddress
import json
import subprocess
import time
from pathlib import Path
from .bench import adb_run,snapshot

def run(adb,ip,sender,receiver,mbps,seconds,hz):
    before=snapshot(adb)
    proc=subprocess.Popen([adb,'shell',receiver,'45200',str(seconds+3),
        '--deadline-us',str(round(1e6/hz))],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        time.sleep(.5)
        sending=subprocess.run([str(sender),str(ip),'45200',str(mbps),str(seconds),str(hz)],
            capture_output=True,text=True,timeout=seconds+20)
        if sending.returncode:raise RuntimeError('Native sender failed: '+sending.stderr+ sending.stdout)
        received,err=proc.communicate(timeout=10)
        if proc.returncode:raise RuntimeError('Android receiver failed: '+err)
        sent=json.loads(sending.stdout);rx=json.loads(received)
    finally:
        if proc.poll() is None:proc.terminate();proc.wait(timeout=5)
    loss=max(0,sent['sent_packets']-rx['received'])
    return {'schema_version':1,'type':'network_only','sender':sent,'receiver':rx,
        'loss_percent':100*loss/sent['sent_packets'] if sent['sent_packets'] else None,
        'delivered_mbps_normalized_to_sender_window':rx['bytes']*8/sent['seconds']/1e6,
        'frames_on_time_percent_of_sent':100*rx.get('frames_complete_on_time',0)/sent['frames_sent'] if sent['frames_sent'] else None,
        'state_before':before,'state_after':snapshot(adb),
        'one_way_latency_ms':None,
        'method':'1400-byte frame bursts. Sender and receiver clocks are unsynchronized; deadline is relative to first received packet. Normalized Mbps integrates received bytes over sender duration.'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb',default='adb');p.add_argument('--ip',required=True,type=ipaddress.IPv4Address)
    p.add_argument('--sender',default=str(Path(__file__).with_name('network_sender.exe')))
    p.add_argument('--receiver',default='/data/local/tmp/q3pw/udprecv-android')
    p.add_argument('--rates',type=int,nargs='+',default=[600,800,1000,1500,2000]);p.add_argument('--seconds',type=int,default=10)
    p.add_argument('--hz',type=int,default=90);p.add_argument('--out',required=True)
    a=p.parse_args()
    if not 1<=a.seconds<=900 or not 1<=a.hz<=240 or any(r<1 or r>2000 for r in a.rates):p.error('Invalid test bounds')
    root=Path(a.out);root.mkdir(parents=True,exist_ok=False);rows=[]
    for rate in a.rates:
        row=run(a.adb,a.ip,a.sender,a.receiver,rate,a.seconds,a.hz);rows.append(row)
        (root/f'udp-{rate}.json').write_text(json.dumps(row,indent=2),encoding='utf-8')
        print(json.dumps({k:row[k] for k in ('loss_percent','delivered_mbps_normalized_to_sender_window','frames_on_time_percent_of_sent')}),flush=True)
        time.sleep(2)
    (root/'summary.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')

if __name__=='__main__':main()
