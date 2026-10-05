"""Verify saved one-shot Surface enqueue/layer/fence/retirement evidence."""
import argparse
import json
import math
import re
from pathlib import Path
from .runtime_logs import PREFIX

FACTS = {
    'instance_dependencies': '[Q3PW_SURFACE_CHART_CAPS] surface_maintenance1=1 get_caps2=1',
    'device_feature': '[Q3PW_SURFACE_CHART_CAPS] swapchain_maintenance1=1 feature=1',
    'prepared': '[Q3PW_SURFACE_CHART] prepared=true submitted=false keep_gles=true',
    'visible_write': '[Q3PW_SURFACE_CHART] write_allowed=true visible=true',
    'fences': '[Q3PW_SURFACE_CHART] fences_ready=true gpu_done=true present_done=true',
    'layer': '[Q3PW_SURFACE_CHART] layer_submitted=true xr_end_ok=true static_only=true',
    'stopping': '[Q3PW_SURFACE_CHART] stopping=true writes_disabled=true',
    'retired': '[Q3PW_SURFACE_CHART] retired=true gpu_done=true present_done=true',
    'producer_destroyed': '[Q3PW_SURFACE_CHART] producer_destroyed=true',
}
ENQUEUE = re.compile(r'\[Q3PW_SURFACE_CHART\] present_result=(0|1000001003) image=(\d+) extent=64x32 format=(37|44) one_shot=true\s*$')


def parse(lines, start, end, pid):
    if not all(math.isfinite(t) for t in (start,end)) or not 0 <= start < end:
        raise ValueError('Need finite device-clock epoch interval')
    if type(pid) is not int or not 0 < pid < 2**31:
        raise ValueError('Need recorded client PID')
    found = {key: [] for key in FACTS}
    enqueues, failures = [], []
    for line in lines:
        if '[Q3PW_SURFACE_CHART' not in line:
            continue
        p = PREFIX.match(line)
        if not p:
            failures.append('unprefixed_record')
            continue
        stamp = float(p[1])
        if int(p[2]) != pid or not start <= stamp <= end:
            continue
        message = line[line.index('[Q3PW_SURFACE_CHART'):].strip()
        if any(word in message for word in ('failed=', 'unsupported', 'preserve_inflight=true')):
            failures.append(message)
        for key, text in FACTS.items():
            if message == text:
                found[key].append(stamp)
        if 'present_result=' in message:
            m = ENQUEUE.fullmatch(message)
            if not m:
                failures.append('invalid_enqueue')
            else:
                enqueues.append({'epoch': stamp,'image':int(m[2]),'format':int(m[3])})
    # Retirement's bounded waits prove both completions even if the nonblocking
    # poll did not observe presentation retirement before STOPPING.
    complete = (all(len(v) == 1 for k,v in found.items() if k != 'fences')
                and len(found['fences']) <= 1 and len(enqueues) == 1 and not failures)
    if complete:
        at = lambda key: found[key][0]
        enqueue = enqueues[0]['epoch']
        complete = (at('instance_dependencies') <= at('prepared') and at('device_feature') <= at('prepared')
            and at('prepared') <= at('visible_write') <= enqueue <= at('layer') <= at('stopping')
            and (not found['fences'] or enqueue <= at('fences') <= at('retired'))
            and at('stopping') <= at('retired') <= at('producer_destroyed'))
    return {'status':'one_shot_lifecycle_verified' if complete else 'incomplete_or_failed',
        'fact_counts':{key:len(v) for key,v in found.items()}, 'enqueue_count':len(enqueues),
        'image_index':enqueues[0]['image'] if complete else None,
        'format':enqueues[0]['format'] if complete else None,'failures':failures,
        'nonblocking_poll_observed_both_fences':bool(found['fences']) if complete else None,
        'performance_acceptance':False,'optical_presentation_verified':False,
        'scope':'Process-specific ordered enqueue, successful XR layer call and independent GPU/presentation retirement. Screenshot/image, optical display, live video ownership/pose pairing and performance remain separate.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('log',type=Path)
    ap.add_argument('--start',type=float,required=True)
    ap.add_argument('--end',type=float,required=True)
    ap.add_argument('--pid',type=int,required=True)
    a = ap.parse_args()
    r = parse(a.log.read_text(encoding='utf-8',errors='replace').splitlines(),a.start,a.end,a.pid)
    print(json.dumps(r,indent=2))
    return 0 if r['status'] == 'one_shot_lifecycle_verified' else 2


if __name__ == '__main__':
    raise SystemExit(main())
