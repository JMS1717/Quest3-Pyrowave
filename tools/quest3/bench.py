"""python -m tools.quest3.bench: capability-gated plans, captures and summaries."""
import argparse
import json
import math
import random
import re
import subprocess
import time
from pathlib import Path

RATES = (90, 120, 144, 207, 240)
BITRATES = (600, 800, 1000, 1500, 2000)

def supported(requested, rates):
    return any(math.isfinite(r) and r > 0 and abs(r - requested) < .01 for r in rates)

def parse_capabilities(log):
    matches = re.findall(r'\[Q3PW_CAPS\] model=(\S+) rates=\[([^]]*)\] runtime=(true|false)', log)
    if not matches:
        raise ValueError('No Quest3-Pyrowave runtime capability log. Launch the built APK first.')
    model, values, runtime = matches[-1]
    rates = [float(v.strip()) for v in values.split(',') if v.strip()]
    return {'model': model, 'rates_hz': sorted(set(r for r in rates if math.isfinite(r) and r > 0)),
            'refresh_extension': runtime == 'true', 'source': 'OpenXR runtime enumeration'}

def plan(caps, repeats=3, seed=1717, seconds=60):
    if not caps.get('refresh_extension'):
        raise ValueError('Refresh extension not advertised; a fallback rate is not a measured capability.')
    cells = []
    skipped = []
    for hz in RATES:
        if not supported(hz, caps['rates_hz']):
            skipped.append({'requested_hz': hz, 'status': 'unsupported', 'reason': 'not advertised by runtime'})
            continue
        for mbps in BITRATES:
            for path in ('Compute', 'Fragment'):
                for rep in range(repeats):
                    cells.append({'id': f'pyro-{hz}-{mbps}-{path.lower()}-r{rep+1}',
                        'codec': 'PyroWave', 'requested_hz': hz, 'mbps': mbps,
                        'decode_path': path, 'wavelet': 'Cdf97', 'replicate': rep+1,
                        'seconds': seconds, 'status': 'planned', 'frame_budget_ms': 1000/hz,
                        'budget_bytes_per_frame': mbps*1e6/8/hz})
        for codec in ('H264', 'Hevc', 'AV1'):
            for rep in range(repeats):
                cells.append({'id': f'{codec.lower()}-{hz}-200-r{rep+1}', 'codec': codec,
                    'requested_hz': hz, 'mbps': 200, 'replicate': rep+1, 'seconds': seconds,
                    'status': 'planned', 'decode_path': None})
    random.Random(seed).shuffle(cells)
    return {'schema_version': 1, 'capabilities': caps, 'seed': seed, 'cells': cells, 'skipped': skipped,
            'method': 'Interleaved repetitions; same scene, resolution and foveation; cool between cells.'}

def distribution(values):
    v = sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not v: return None
    def percentile(p):
        i = (len(v)-1)*p; lo=int(i); hi=min(lo+1,len(v)-1)
        return v[lo]+(v[hi]-v[lo])*(i-lo)
    return {'n':len(v), 'p01':percentile(.01), 'p50':percentile(.5),
            'p95':percentile(.95), 'p99':percentile(.99), 'max':v[-1]}

def summarise(events, requested_hz=None):
    graphs=[]; summaries=[]; telemetry=[]
    for item in events:
        event=item.get('event',item).get('event_type',{})
        data=event.get('data',{})
        if event.get('id')=='GraphStatistics': graphs.append(data)
        if event.get('id')=='StatisticsSummary': summaries.append(data)
        if event.get('id')=='HeadsetTelemetry': telemetry.append(data)
    result={'schema_version':1,'status':'measured' if graphs else 'no_stream_frames', 'frames':len(graphs),
            'requested_hz':requested_hz, 'metrics':{}, 'headset_telemetry':telemetry,
            'optical_motion_to_photon_ms':None,
            'latency_definition':'ALVR estimated pipeline; optical motion-to-photon requires a separate camera/photodiode measurement.'}
    for field in ('encoder_s','decoder_s','network_s','total_pipeline_latency_s',
                  'decoder_queue_s','server_compositor_s','client_compositor_s','vsync_queue_s'):
        result['metrics'][field.replace('_s','_ms')]=distribution([g.get(field,0)*1000 for g in graphs if field in g])
    result['metrics']['client_fps']=distribution([g.get('client_fps') for g in graphs])
    result['metrics']['server_fps']=distribution([g.get('server_fps') for g in graphs])
    result['metrics']['video_mbps']=distribution([g.get('bitrate_bps',0)/1e6 for g in graphs])
    timestamps=sorted(set(g['target_timestamp_ns'] for g in graphs if 'target_timestamp_ns' in g))
    result['metrics']['frame_timestamp_gap_ms']=distribution([(b-a)/1e6 for a,b in zip(timestamps,timestamps[1:])])
    # Counter deltas; never report the last lifetime total as this capture's losses.
    result['packet_loss_delta']=None
    if len(summaries)>1 and 'packets_lost_total' in summaries[0]:
        delta=summaries[-1]['packets_lost_total']-summaries[0]['packets_lost_total']
        result['packet_loss_delta']=delta if delta>=0 else None
    result['gpu_decode_ms']=distribution([v for t in telemetry if t.get('pyrowave')
        for v in t['pyrowave'].get('gpu_decode_ms',[])])
    result['decode_to_fence_ms']=distribution([v for t in telemetry if t.get('pyrowave')
        for v in t['pyrowave'].get('fence_ms',[])])
    if requested_hz and graphs:
        fps=result['metrics']['client_fps']['p50']
        result['sustained_requested_fps']=fps>=requested_hz*.98
    return result

def adb_run(adb, *args):
    p=subprocess.run([adb,*args],capture_output=True,text=True,timeout=20)
    if p.returncode: raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return p.stdout

def snapshot(adb):
    # No root or clock overrides. Inaccessible counters remain explicit errors.
    result={}
    for key,cmd in {'battery':'dumpsys battery','thermals':'dumpsys thermalservice',
        'gpu_clock_hz':'cat /sys/class/kgsl/kgsl-3d0/gpuclk',
        'gpu_busy':'cat /sys/class/kgsl/kgsl-3d0/gpubusy',
        'gpu_available_frequencies':'cat /sys/class/kgsl/kgsl-3d0/devfreq/available_frequencies'}.items():
        try: result[key]={'value':adb_run(adb,'shell',cmd),'error':None}
        except (RuntimeError,subprocess.TimeoutExpired) as e: result[key]={'value':None,'error':str(e)}
    return result

def capture(args):
    import websocket
    root=Path(args.out);root.mkdir(parents=True,exist_ok=False)
    start=snapshot(args.adb);events=[];samples=[];error=None
    try:
        ws=websocket.create_connection(args.events,header=['X-ALVR: true'],timeout=3)
        begin=time.monotonic();next_sample=begin
        with (root/'events.jsonl').open('w',encoding='utf-8') as out:
            while time.monotonic()-begin<args.seconds:
                try:
                    event=json.loads(ws.recv())
                    if event.get('event_type',{}).get('id') in ('GraphStatistics','StatisticsSummary','HeadsetTelemetry'):
                        row={'capture_elapsed_s':time.monotonic()-begin,'event':event}
                        events.append(row);out.write(json.dumps(row)+'\n')
                except websocket.WebSocketTimeoutException: pass
                if time.monotonic()>=next_sample:
                    samples.append({'elapsed_s':time.monotonic()-begin, 'state':snapshot(args.adb)})
                    next_sample=time.monotonic()+5
        ws.close()
    except Exception as e: error=str(e)
    report=summarise(events,args.hz);report.update({'duration_requested_s':args.seconds,
        'error':error,'state_start':start,'state_end':snapshot(args.adb),'device_samples':samples})
    if error: report['status']='capture_failed'
    (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'frames':report['frames'],'out':str(root)}))
    return 0 if report['status']=='measured' else 1

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capabilities');c.add_argument('--adb',default='adb');c.add_argument('--out',required=True)
    c=sub.add_parser('plan');c.add_argument('--capabilities',required=True);c.add_argument('--out',required=True)
    c.add_argument('--repeats',type=int,default=3);c.add_argument('--seconds',type=int,default=60)
    c=sub.add_parser('capture');c.add_argument('--adb',default='adb');c.add_argument('--out',required=True)
    c.add_argument('--seconds',type=int,default=60);c.add_argument('--hz',type=int,required=True)
    c.add_argument('--events',default='ws://127.0.0.1:8082/api/events')
    c=sub.add_parser('summarise');c.add_argument('events');c.add_argument('--out',required=True)
    a=p.parse_args()
    if a.command=='capture': return capture(a)
    if a.command=='capabilities': data=parse_capabilities(adb_run(a.adb,'logcat','-d','-t','4000'))
    elif a.command=='plan': data=plan(json.loads(Path(a.capabilities).read_text()),a.repeats,seconds=a.seconds)
    else: data=summarise([json.loads(line) for line in Path(a.events).read_text().splitlines()])
    Path(a.out).write_text(json.dumps(data,indent=2),encoding='utf-8');print(a.out);return 0

if __name__=='__main__': raise SystemExit(main())
