"""Explicit ALVR session changes for reproducible Quest experiments."""
import argparse
import json
import urllib.request
import subprocess
import time
import os
from pathlib import Path
from .bench import supported

API='http://127.0.0.1:8082/api/dashboard-request'
EVENTS='ws://127.0.0.1:8082/api/events'
def request(value):
    req=urllib.request.Request(API,data=json.dumps(value).encode(),
        headers={'X-ALVR':'true','Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as response:
        if response.status!=200: raise RuntimeError(f'ALVR HTTP {response.status}')

def session():
    import websocket
    ws=websocket.create_connection(EVENTS,header=['X-ALVR: true'],timeout=10)
    try:
        request('GetSession')
        for _ in range(1000):
            event=json.loads(ws.recv()).get('event_type',{})
            if event.get('id')=='Session': return event['data']
        raise RuntimeError('No session response')
    finally: ws.close()

def set_values(values):
    request({'SetValues':[{'path':[{'Name':s} for s in path.split('.')],'value':value}
        for path,value in values.items()]})

def apply(codec, mbps, hz, path, caps):
    if not caps.get('refresh_extension') or not supported(hz,caps['rates_hz']):
        raise ValueError(f'{hz} Hz unsupported by provided runtime capabilities')
    if hz>120 and caps.get('source')!='request_and_frame_period':
        raise ValueError('Extended rates require current APK startup probe; enumeration is incomplete on HorizonOS v2.7.')
    if codec not in ('PyroWave','H264','Hevc','AV1') or not (1<=mbps<=2000):
        raise ValueError('Invalid codec or bitrate')
    values={'session_settings.video.preferred_codec.variant':codec,
            'session_settings.video.preferred_fps':hz,
            'session_settings.video.bitrate.mode.variant':'ConstantMbps',
            'session_settings.video.bitrate.mode.ConstantMbps':mbps,
            'session_settings.video.enforce_server_frame_pacing':True,
            'session_settings.video.foveated_encoding.content.follow_gaze':False,
            'session_settings.video.foveated_encoding.enabled':False,
            'session_settings.video.clientside_foveation.enabled':False}
    if codec=='PyroWave':
        if path not in ('Auto','Compute','Fragment'): raise ValueError('Invalid decode path')
        values.update({'session_settings.video.pyrowave.transport.variant':'Udp',
                       'session_settings.video.pyrowave.wavelet.variant':'Cdf97',
                       'session_settings.video.pyrowave.decode_path.variant':path})
    set_values(values)
    current=session()
    for key,value in values.items():
        node=current
        for field in key.split('.'):node=node[field]
        if node!=value:raise RuntimeError(f'Setting rejected: {key}')
    return {'codec':codec,'mbps':mbps,'requested_hz':hz,'decode_path':path,'verified':True}

def restart(steamvr,streamer=None):
    # The HTTP request shuts the server down; only the dashboard's UI also launches it.
    # Re-register this driver after upstream restores its registration backup on shutdown.
    s=session()
    if streamer is None:streamer=s.get('drivers_backup',{}).get('alvr_path') if s.get('drivers_backup') else None
    if not streamer:raise ValueError('Specify --streamer: no driver path in session backup')
    driver=Path(streamer).resolve();runtime=Path(steamvr).resolve()
    reg=runtime/'bin/win64/vrpathreg.exe';startup=runtime/'bin/win64/vrstartup.exe'
    if not (driver/'driver.vrdrivermanifest').is_file() or not reg.is_file() or not startup.is_file():
        raise ValueError('Invalid SteamVR runtime or streamer directory')
    request('RestartSteamvr')
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        tasks=subprocess.run(['tasklist','/FI','IMAGENAME eq vrserver.exe','/FO','CSV','/NH'],capture_output=True,text=True,timeout=5)
        if 'vrserver.exe' not in tasks.stdout.lower():break
        time.sleep(.5)
    else:raise RuntimeError('SteamVR did not shut down; leaving registrations unchanged')
    subprocess.run([str(reg),'adddriver',str(driver)],check=True,capture_output=True)
    info=subprocess.STARTUPINFO();info.dwFlags|=subprocess.STARTF_USESHOWWINDOW;info.wShowWindow=0
    subprocess.Popen([str(startup)],startupinfo=info)
    print('SteamVR launch requested; wait for streaming to settle before capture')


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='cmd',required=True)
    sub.add_parser('status');r=sub.add_parser('restart')
    r.add_argument('--steamvr',required=True);r.add_argument('--streamer')
    c=sub.add_parser('apply');c.add_argument('--codec',choices=['PyroWave','H264','Hevc','AV1'],default='PyroWave')
    c.add_argument('--mbps',type=int,required=True);c.add_argument('--hz',type=int,required=True)
    c.add_argument('--decode-path',choices=['Auto','Compute','Fragment'],default='Auto');c.add_argument('--capabilities',required=True)
    a=parser.parse_args()
    if a.cmd=='restart':restart(a.steamvr,a.streamer);return
    if a.cmd=='apply':print(json.dumps(apply(a.codec,a.mbps,a.hz,a.decode_path,json.loads(Path(a.capabilities).read_text()))));return
    s=session();v=s['session_settings']['video'];clients=s.get('client_connections',{})
    print(json.dumps({'video':{key:v.get(key) for key in ('preferred_codec','preferred_fps','bitrate','pyrowave','transcoding_view_resolution')},
                     'client_count':len(clients)}))

if __name__=='__main__':main()
