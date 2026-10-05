"""Explicit ALVR session changes for reproducible Quest experiments."""
import argparse
import json
import urllib.request
import subprocess
import time
import os
from pathlib import Path
from .bench import supported
from .resolution import assignments, profiles, aligned_eye, RENDER_FIELD, ENCODE_FIELD

API='http://127.0.0.1:8082/api/dashboard-request'
EVENTS='ws://127.0.0.1:8082/api/events'

def windows_process_running(executable):
    """Read the process snapshot directly; tasklist can hang during SteamVR shutdown."""
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('name', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for method in (kernel.Process32FirstW, kernel.Process32NextW):
        method.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        method.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = Entry(); entry.size = ctypes.sizeof(Entry)
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            if entry.name.casefold() == executable.casefold(): return True
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18: # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
        return False
    finally:
        kernel.CloseHandle(handle)
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

def foveation(mode, profile=None):
    if mode not in ('off', 'light'):
        raise ValueError('Use off or light')
    if profile is not None and profile not in ('light', 'balanced', 'strong'):
        raise ValueError('Profile must be light, balanced or strong')
    before = session()
    pyro = before['session_settings']['video']['pyrowave']
    if 'light_foveated_encoding' not in pyro:
        raise ValueError('Light encoding requires the matching .28 or newer development pair')
    if profile is not None and 'foveation_profile' not in pyro:
        raise ValueError('Foveation profiles require a matching server with the profile setting')
    key = 'session_settings.video.pyrowave.light_foveated_encoding'
    values = {key: mode == 'light'}
    profile_key = 'session_settings.video.pyrowave.foveation_profile.variant'
    if profile is not None:
        values[profile_key] = profile.capitalize()
    set_values(values)
    after = session()['session_settings']['video']['pyrowave']
    if after['light_foveated_encoding'] != (mode == 'light'):
        raise RuntimeError('Light encoding setting rejected')
    if profile is not None and after['foveation_profile']['variant'] != profile.capitalize():
        raise RuntimeError('Foveation profile setting rejected')
    result = {'mode': mode, 'previous_enabled': pyro['light_foveated_encoding'],
            'settings_verified': True, 'steamvr_restart_required': True,
            'perceptual_acceptance': False, 'sustained_performance_verified': False}
    if profile is not None:
        result.update(profile=profile, previous_profile=pyro['foveation_profile']['variant'].lower())
    return result

def latency_stamp(enabled):
    """Toggle the server's optical latency stamp (diagnostic only; see docs/OPTICAL-LATENCY.md)."""
    before = session()
    pyro = before['session_settings']['video']['pyrowave']
    if 'latency_stamp' not in pyro:
        raise ValueError('The latency stamp requires a matching server with the latency_stamp setting')
    previous = bool(pyro['latency_stamp'])
    set_values({'session_settings.video.pyrowave.latency_stamp': bool(enabled)})
    after = session()['session_settings']['video']['pyrowave']
    if after['latency_stamp'] != bool(enabled):
        raise RuntimeError('Latency stamp setting rejected')
    return {'latency_stamp': bool(enabled), 'previous': previous,
            'settings_verified': True, 'steamvr_restart_required': True}

def downsample(mode):
    """PC composition filter from game render to stream size, for render-size A/B screens."""
    variant = {'adaptive': 'Adaptive', 'bilinear': 'Bilinear'}.get(mode)
    if variant is None:
        raise ValueError('Use adaptive or bilinear')
    pyro = session()['session_settings']['video']['pyrowave']
    if 'render_downsample_filter' not in pyro:
        raise ValueError('The downsample filter requires a streamer with the adaptive filter')
    previous = pyro['render_downsample_filter']['variant']
    set_values({'session_settings.video.pyrowave.render_downsample_filter.variant': variant})
    if session()['session_settings']['video']['pyrowave']['render_downsample_filter']['variant'] != variant:
        raise RuntimeError('Downsample filter setting rejected')
    return {'mode': mode, 'previous': previous, 'settings_verified': True,
            'steamvr_restart_required': True, 'perceptual_acceptance': False}

def resolution(render_eye=None, encode_eye=None, profile=None):
    if profile is not None:
        if render_eye is not None or encode_eye is not None:
            raise ValueError('Use a profile or explicit eye dimensions, not both')
        selected = profiles().get(profile)
        if selected is None: raise ValueError('Unknown resolution profile')
        render_eye, encode_eye = selected['render_eye'], selected['encode_eye']
    values = assignments(render_eye, encode_eye)
    before = session()
    previous = {field:before['session_settings']['video'].get(field)
                for field in (RENDER_FIELD, ENCODE_FIELD)}
    set_values(values)
    current = session()
    for key, value in values.items():
        node = current
        for field in key.split('.'): node = node[field]
        if node != value: raise RuntimeError(f'Setting rejected: {key}')
    return {'settings_verified': True, 'steamvr_restart_required': True,
            'render_eye_requested': render_eye, 'encode_eye_requested': encode_eye,
            'render_eye_aligned': aligned_eye(render_eye), 'encode_eye_aligned': aligned_eye(encode_eye),
            'previous_resolution_settings': previous,
            'sustained_performance_verified': False}

def usb(enabled):
    if enabled:
        set_values({'session_settings.connection.wired_client_type.variant':'Custom',
                    'session_settings.connection.wired_client_type.Custom':'io.github.jms1717.quest3pyrowave',
                    'session_settings.video.pyrowave.transport.variant':'Tcp',
                    'session_settings.connection.stream_protocol.variant':'Tcp'})
    request({'UpdateClientList':{'hostname':'client.wired',
        'action':{'AddIfMissing':{'trusted':True,'manual_ips':[]}} if enabled else 'RemoveEntry'}})

def apply(codec, mbps, hz, path, caps, chroma="420", transport="Tcp", wavelet="Cdf97"):
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
        if transport not in ('Tcp', 'Udp'): raise ValueError('Invalid transport')
        if chroma not in ('420','444'): raise ValueError('Invalid chroma')
        if path not in ('Auto','Compute','Fragment'): raise ValueError('Invalid decode path')
        if wavelet not in ('Cdf97','Cdf53','Haar'): raise ValueError('Invalid wavelet')
        if wavelet in ('Cdf53','Haar') and path == 'Fragment':
            raise ValueError('CDF 5/3 and Haar require Compute or Auto decode')
        values.update({'session_settings.video.pyrowave.chroma_444':chroma=='444',
                       'session_settings.video.pyrowave.transport.variant':transport,
                       'session_settings.connection.stream_protocol.variant':'Tcp',
                       'session_settings.video.pyrowave.wavelet.variant':wavelet,
                       'session_settings.video.pyrowave.decode_path.variant':path})
    set_values(values)
    current=session()
    for key,value in values.items():
        node=current
        for field in key.split('.'):node=node[field]
        if node!=value:raise RuntimeError(f'Setting rejected: {key}')
    return {'codec':codec,'mbps':mbps,'requested_hz':hz,'decode_path':path,'chroma':chroma,
            'transport':transport if codec=='PyroWave' else None,'settings_verified':True,
            'wavelet':wavelet if codec=='PyroWave' else None,
            'sustained_performance_verified':False}

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
        if not windows_process_running('vrserver.exe'):break
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
    r=sub.add_parser('resolution',help='Independent geometry only; requires a SteamVR restart')
    r.add_argument('--render-eye',type=int,nargs=2,metavar=('WIDTH','HEIGHT'))
    r.add_argument('--encode-eye',type=int,nargs=2,metavar=('WIDTH','HEIGHT'))
    r.add_argument('--profile',choices=tuple(profiles()))
    f=sub.add_parser('foveation');f.add_argument('--mode',choices=['off','light'],required=True)
    d=sub.add_parser('downsample',help='Game render -> stream filter; requires a SteamVR restart')
    d.add_argument('--mode',choices=['adaptive','bilinear'],required=True)
    f.add_argument('--profile',choices=['light','balanced','strong'])
    ls=sub.add_parser('latency-stamp',help='Diagnostic clock stamp in each eye; requires a SteamVR restart')
    ls.add_argument('--state',choices=['on','off'],required=True)
    u=sub.add_parser('usb');g=u.add_mutually_exclusive_group(required=True)
    g.add_argument('--enable',action='store_true');g.add_argument('--disable',action='store_true')
    c=sub.add_parser('apply');c.add_argument('--codec',choices=['PyroWave','H264','Hevc','AV1'],default='PyroWave')
    c.add_argument('--mbps',type=int,required=True);c.add_argument('--hz',type=int,required=True)
    c.add_argument('--decode-path',choices=['Auto','Compute','Fragment'],default='Auto');c.add_argument('--capabilities',required=True)
    c.add_argument('--chroma',choices=['420','444'],default='420')
    c.add_argument('--wavelet',choices=['Cdf97','Cdf53','Haar'],default='Cdf97')
    c.add_argument('--transport',choices=['Tcp','Udp'],default='Tcp');a=parser.parse_args()
    if a.cmd=='restart':restart(a.steamvr,a.streamer);return
    if a.cmd=='resolution':print(json.dumps(resolution(a.render_eye,a.encode_eye,a.profile)));return
    if a.cmd=='foveation':print(json.dumps(foveation(a.mode,a.profile)));return
    if a.cmd=='downsample':print(json.dumps(downsample(a.mode)));return
    if a.cmd=='latency-stamp':print(json.dumps(latency_stamp(a.state=='on')));return
    if a.cmd=='usb':usb(a.enable);print('USB mode enabled; restart SteamVR if transport changed' if a.enable else 'USB mode disabled');return
    if a.cmd=='apply':print(json.dumps(apply(a.codec,a.mbps,a.hz,a.decode_path,json.loads(Path(a.capabilities).read_text()),a.chroma,a.transport,a.wavelet)));return
    s=session();v=s['session_settings']['video'];clients=s.get('client_connections',{})
    print(json.dumps({'video':{key:v.get(key) for key in ('preferred_codec','preferred_fps','bitrate','pyrowave',ENCODE_FIELD,RENDER_FIELD)},
                     'openvr_config':{key:s.get('openvr_config',{}).get(key) for key in
                        ('eye_resolution_width','eye_resolution_height','target_eye_resolution_width','target_eye_resolution_height','render_downsample_filter')},
                     'client_count':len(clients)}))

if __name__=='__main__':main()
