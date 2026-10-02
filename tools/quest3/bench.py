"""python -m tools.quest3.bench: capability-gated plans, captures and summaries."""
import argparse
import json
import math
import random
import re
import subprocess
import time
import threading
from pathlib import Path

RATES = (72, 90, 120, 144, 207, 240)
BITRATES = (400, 600, 800, 1000, 1500, 2000)

def supported(requested, rates):
    return any(math.isfinite(r) and r > 0 and abs(r - requested) < .01 for r in rates)

def parse_capabilities(log):
    matches = list(re.finditer(r'\[Q3PW_CAPS\] model=(\S+) rates=\[([^]]*)\] runtime=(true|false)(?: source=(\w+))?', log))
    if not matches:
        raise ValueError('No Quest3-Pyrowave runtime capability log. Launch the built APK first.')
    model, values, runtime, source = matches[-1].groups()
    rates = [float(v.strip()) for v in values.split(',') if v.strip()]
    begin = matches[-2].end() if len(matches)>1 else 0
    probes = [{'requested_hz':float(hz), 'confirmed':ok=='true'} for hz,ok in
        re.findall(r'\[Q3PW_PROBE\] request=([0-9.]+) confirmed=(true|false)', log[begin:matches[-1].start()])]
    return {'model':model, 'rates_hz':sorted(set(r for r in rates if math.isfinite(r) and r>0)),
            'refresh_extension':runtime=='true', 'source':'request_and_frame_period' if source=='probe' else 'enumeration',
            'probe_results':probes}


def plan(caps, repeats=3, seed=1717, seconds=15):
    if not caps.get('refresh_extension'):
        raise ValueError('Refresh extension not advertised; a fallback rate is not a measured capability.')
    cells = []
    skipped = []
    for hz in RATES:
        if not supported(hz, caps['rates_hz']):
            skipped.append({'requested_hz':hz, 'status':'not_confirmed',
                'reason':('startup probe did not confirm this mode' if caps.get('source')=='request_and_frame_period'
                    else 'enumeration alone is incomplete; launch current APK for request/frame-period probing'),
                'requires_display_scaling':hz>207})
            continue
        for mbps in BITRATES:
            for path in ('Compute', 'Fragment'):
                for rep in range(repeats):
                    cells.append({'id': f'pyro-{hz}-{mbps}-{path.lower()}-r{rep+1}',
                        'codec': 'PyroWave', 'requested_hz': hz, 'mbps': mbps,
                        'decode_path': path, 'wavelet': 'Cdf97', 'chroma': '420', 'transport': 'Tcp', 'replicate': rep+1,
                        'seconds': seconds, 'status': 'planned', 'frame_budget_ms': 1000/hz,
                        'budget_bytes_per_frame': mbps*1e6/8/hz})
        for codec in ('H264', 'Hevc', 'AV1'):
            for rep in range(repeats):
                cells.append({'id': f'{codec.lower()}-{hz}-200-r{rep+1}', 'codec': codec,
                    'requested_hz': hz, 'mbps': 200, 'replicate': rep+1, 'seconds': seconds,
                    'status': 'planned', 'decode_path': None})
    random.Random(seed).shuffle(cells)
    return {'schema_version': 1, 'capabilities': caps, 'seed': seed, 'cells': cells, 'skipped': skipped,
            'method': 'Quick screening with interleaved repetitions; same scene and resolution; cool between cells. Longer explicit runs are required for sustained and thermal acceptance.'}

def distribution(values):
    v = sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not v: return None
    def percentile(p):
        i = (len(v)-1)*p; lo=int(i); hi=min(lo+1,len(v)-1)
        return v[lo]+(v[hi]-v[lo])*(i-lo)
    return {'n':len(v), 'p01':percentile(.01), 'p50':percentile(.5),
            'p95':percentile(.95), 'p99':percentile(.99), 'max':v[-1]}

PYROWAVE_COUNTERS = (
    'complete', 'partial', 'skipped', 'dropped', 'superseded', 'stale_packets',
    'decode_failures', 'direct_eye_copies', 'staging_eye_copies',
    'completed_eye_copies', 'pending_eye_copy_deferrals',
)


def pyrowave_counter_window(samples):
    """Use the same endpoints for every producer/consumer counter and rate.

    Missing or reset counters remain unknown. An invalid/non-increasing timestamp
    anywhere in this window invalidates every rate, without hiding counter deltas.
    These counters do not measure unique optical presentations.
    """
    deltas={}
    for field in PYROWAVE_COUNTERS:
        values=[data.get(field) for _,data in samples]
        valid=len(values)>1 and all(isinstance(v,int) and not isinstance(v,bool) and v>=0 for v in values)
        monotonic=valid and all(b>=a for a,b in zip(values,values[1:]))
        deltas[field]=values[-1]-values[0] if monotonic else None
    times=[elapsed for elapsed,_ in samples]
    valid_times=len(times)>1 and all(isinstance(t,(int,float)) and not isinstance(t,bool)
        and math.isfinite(t) and t>=0 for t in times)
    increasing=valid_times and all(b>a for a,b in zip(times,times[1:]))
    span=times[-1]-times[0] if increasing else None
    return {'samples':len(samples), 'interval_s':span, 'counter_deltas':deltas,
        'counter_rates_per_s':{name:delta/span if span is not None and delta is not None else None
            for name,delta in deltas.items()},
        'definition':'Matching HeadsetTelemetry endpoints. Complete is producer decode completion; superseded includes pending replacement and out-of-order publication. Eye completion can include configuration redraws; staging completion is unobserved. Not unique or optical display FPS.'}


def summarise(events, requested_hz=None):
    graphs=[]; summaries=[]; telemetry=[]; graph_times=[]; pyro_samples=[]
    for item in events:
        event=item.get('event',item).get('event_type',{})
        data=event.get('data',{})
        if event.get('id')=='GraphStatistics':
            graphs.append(data)
            elapsed=item.get('capture_elapsed_s')
            if isinstance(elapsed,(int,float)) and math.isfinite(elapsed):
                graph_times.append(elapsed)
        if event.get('id')=='StatisticsSummary': summaries.append(data)
        if event.get('id')=='HeadsetTelemetry':
            telemetry.append(data)
            elapsed=item.get('capture_elapsed_s')
            if data.get('pyrowave'):
                pyro_samples.append((elapsed,data['pyrowave']))
    result={'schema_version':1,'status':'measured' if graphs else 'no_stream_frames', 'frames':len(graphs),
            'requested_hz':requested_hz, 'metrics':{}, 'headset_telemetry':telemetry,
            'optical_motion_to_photon_ms':None,
            'latency_definition':'ALVR estimated pipeline; optical motion-to-photon requires a separate camera/photodiode measurement.'}
    for field in ('encoder_s','decoder_s','network_s','total_pipeline_latency_s',
                  'decoder_queue_s','server_compositor_s','client_compositor_s','vsync_queue_s'):
        result['metrics'][field.replace('_s','_ms')]=distribution([g.get(field,0)*1000 for g in graphs if field in g])
    # Submission-event rate over capture wall time exposes missed slots that a
    # median instantaneous FPS can hide. This is not an optical/display counter.
    span=graph_times[-1]-graph_times[0] if len(graph_times)>1 else 0
    result['submitted_frame_rate_fps']=(len(graph_times)-1)/span if span>0 else None
    result['submission_rate_definition']='GraphStatistics events per capture-time span; submitted video frames, not repeated OpenXR layers. With async copies this is submission, not GPU completion or optical display FPS.'
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
    result['network_latency_definition']='ALVR residual estimate; PyroWave separate UDP timing can clamp this to zero. Not a direct one-way measurement.'
    result['gpu_decode_ms']=distribution([v for t in telemetry if t.get('pyrowave')
        for v in t['pyrowave'].get('gpu_decode_ms',[])])
    result['decode_to_fence_ms']=distribution([v for t in telemetry if t.get('pyrowave')
        for v in t['pyrowave'].get('fence_ms',[])])
    for field in ('convert_ms','record_ms','wait_ms'):
        result['native_'+field]=distribution([v for t in telemetry if t.get('pyrowave')
            for v in t['pyrowave'].get(field,[])])
    for field in ('eye_acquire_wait_ms', 'eye_render_ms', 'eye_release_ms', 'eye_completion_observed_ms'):
        result[field]=distribution([v for t in telemetry if t.get('pyrowave')
            for v in t['pyrowave'].get(field,[])])
    result['eye_timing_definition']='Client CPU wall time: acquire/wait, renderer call, release. Async completion is wall time until a later nonblocking fence poll, quantized by polling; none of these are GPU timer-query durations.'
    window=pyrowave_counter_window(pyro_samples)
    result['pyrowave_counter_window']=window
    result['eye_copy_counter_deltas']={field:window['counter_deltas'][field] for field in (
        'direct_eye_copies', 'staging_eye_copies', 'completed_eye_copies', 'pending_eye_copy_deferrals')}
    result['completed_eye_copy_rate_fps']=None
    if ((result['eye_copy_counter_deltas']['direct_eye_copies'] or 0)>0
            and result['eye_copy_counter_deltas']['staging_eye_copies']==0):
        result['completed_eye_copy_rate_fps']=window['counter_rates_per_s']['completed_eye_copies']
    result['completion_rate_definition']='GPU-complete direct eye copies per telemetry-time span, only for exclusively direct windows; staging completion is unobserved. Includes configuration-forced redraws. Not optical display or unique fresh-frame FPS.'
    if requested_hz and graphs:
        fps=result['metrics']['client_fps']
        submitted=result['submitted_frame_rate_fps']
        result['sustained_requested_fps']=(fps is not None and submitted is not None
            and fps['p01']>=requested_hz*.98 and submitted>=requested_hz*.98)
        completed=result['completed_eye_copy_rate_fps']
        if completed is not None:
            result['sustained_requested_fps'] &= completed>=requested_hz*.98
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

def active_settings():
    from .control import session
    s=session();v=s['session_settings']['video'];o=s.get('openvr_config',{})
    mode = v['bitrate']['mode']
    return {'server_version':s.get('server_version'), 'codec':v['preferred_codec']['variant'],
        'bitrate_mode':mode['variant'], 'bitrate_config':mode,
        'target_mbps':mode['ConstantMbps'] if mode['variant']=='ConstantMbps' else None,
        'requested_hz':v['preferred_fps'], 'decode_path':v['pyrowave']['decode_path']['variant'],
        'wavelet':v['pyrowave']['wavelet']['variant'],
        'chroma':'444' if v['pyrowave'].get('chroma_444',False) else '420',
        'transport':v['pyrowave']['transport']['variant'], 'stream_protocol':s['session_settings']['connection']['stream_protocol']['variant'],
        'configured_view_resolution':v['transcoding_view_resolution'],
        'openvr':{k:o.get(k) for k in ('refresh_rate','eye_resolution_width','eye_resolution_height',
            'target_eye_resolution_width','target_eye_resolution_height','pyrowave_enabled','pyrowave_decode_path','pyrowave_wavelet_53','pyrowave_wavelet_haar','pyrowave_udp','pyrowave_chroma_444','enable_foveated_encoding')}}

def runtime_evidence(adb):
    log=adb_run(adb,'logcat','-d','-t','20000')
    # Store only app diagnostic records, never the complete system log.
    return [line.split(']: ',1)[-1] for line in log.splitlines()
        if re.search(r'\[Q3PW_(CAPS|PROBE|VERIFIED|RATE|EFFECTIVE)\]',line)]

def client_build(adb):
    try:
        package=adb_run(adb,'shell','dumpsys','package','io.github.jms1717.quest3pyrowave')
        version=re.search(r'^\s*versionName=(\S+)',package,re.MULTILINE)
        return {'version':version.group(1) if version else None,
                'error':None if version else 'Package version unavailable'}
    except (RuntimeError,subprocess.TimeoutExpired) as exc:
        return {'version':None,'error':str(exc)}

def capture(args):
    import websocket
    root=Path(args.out);root.mkdir(parents=True,exist_ok=False)
    start=snapshot(args.adb);build=client_build(args.adb);events=[];samples=[];error=None;ws=None
    try:settings_start=active_settings()
    except Exception as exc:
        report={'status':'server_unavailable','frames':0,'error':str(exc),'state_start':start,
                'client_build':build}
        (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'status':report['status'],'out':str(root)}));return 1
    stop=threading.Event();begin_wall_ns=time.time_ns();begin=time.monotonic()
    def sample_device():
        while not stop.is_set():
            samples.append({'elapsed_s':time.monotonic()-begin,'state':snapshot(args.adb)})
            stop.wait(5)
    sampler=threading.Thread(target=sample_device,daemon=True);sampler.start()
    try:
        ws=websocket.create_connection(args.events,header=['X-ALVR: true'],timeout=2)
        with (root/'events.jsonl').open('w',encoding='utf-8') as out:
            while time.monotonic()-begin<args.seconds:
                try:
                    event=json.loads(ws.recv())
                    if event.get('event_type',{}).get('id') in ('GraphStatistics','StatisticsSummary','HeadsetTelemetry'):
                        row={'capture_elapsed_s':time.monotonic()-begin,'event':event}
                        events.append(row);out.write(json.dumps(row)+'\n')
                except websocket.WebSocketTimeoutException: pass
    except Exception as e:error=str(e)
    finally:
        stop.set();sampler.join(timeout=25)
        if ws:ws.close()
    report=summarise(events,args.hz)
    try:settings_end=active_settings()
    except Exception as exc:settings_end=None;error=str(exc)
    report.update({'duration_requested_s':args.seconds,'capture_started_unix_ns':begin_wall_ns,'elapsed_s':time.monotonic()-begin,
        'error':error,'state_start':start,'state_end':snapshot(args.adb),'device_samples':samples,
        'settings_start':settings_start,'settings_end':settings_end,'client_build':build,
        'runtime_evidence':runtime_evidence(args.adb)})
    if settings_start!=settings_end:report['status']='settings_changed_during_capture'
    if settings_start['openvr'].get('refresh_rate')!=args.hz:report['status']='negotiated_rate_mismatch'
    if error:report['status']='capture_failed'
    (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'frames':report['frames'],'out':str(root)}))
    return 0 if report['status']=='measured' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capabilities');c.add_argument('--adb',default='adb');c.add_argument('--out',required=True)
    c=sub.add_parser('plan');c.add_argument('--capabilities',required=True);c.add_argument('--out',required=True)
    c.add_argument('--repeats',type=int,default=3);c.add_argument('--seconds',type=int,default=15)
    c=sub.add_parser('capture');c.add_argument('--adb',default='adb');c.add_argument('--out',required=True)
    c.add_argument('--seconds',type=int,default=15);c.add_argument('--hz',type=int,required=True)
    c.add_argument('--events',default='ws://127.0.0.1:8082/api/events')
    c=sub.add_parser('summarise');c.add_argument('events');c.add_argument('--out',required=True)
    a=p.parse_args()
    if a.command=='capture': return capture(a)
    if a.command=='capabilities': data=parse_capabilities(adb_run(a.adb,'shell','logcat -d -t 20000'))
    elif a.command=='plan': data=plan(json.loads(Path(a.capabilities).read_text()),a.repeats,seconds=a.seconds)
    else: data=summarise([json.loads(line) for line in Path(a.events).read_text().splitlines()])
    Path(a.out).write_text(json.dumps(data,indent=2),encoding='utf-8');print(a.out);return 0

if __name__=='__main__': raise SystemExit(main())
