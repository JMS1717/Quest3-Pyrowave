"""Validate saved creation-only Surface probe evidence; no hardware access."""
import argparse
import json
import math
import re
from pathlib import Path
from .runtime_logs import PREFIX

WSI = re.compile(r'\[Q3PW_SURFACE_WSI\] created=true vendor=(0x[0-9a-f]+) device=(0x[0-9a-f]+) family=(\d+) extent=(\d+)x(\d+) images=(\d+) format=(\d+) usage=(0x[0-9a-f]+) max_extent=(\d+)x(\d+) min_images=(\d+) max_images=(\d+) submitted=false\s*$')
DONE = re.compile(r'\[Q3PW_SURFACE_PROBE\] native_result=(-?\d+) destroyed=true submitted=false keep_gles=true\s*$')


def parse(lines, start, end, pid):
    if not all(math.isfinite(t) for t in (start, end)) or not 0 <= start < end:
        raise ValueError('Need a finite increasing device-clock epoch interval')
    if type(pid) is not int or not 0 < pid < 2**31:
        raise ValueError('Need recorded client PID')
    created, completed, failed = [], [], []
    invalid = 0
    for line in lines:
        if not any(m in line for m in ('[Q3PW_SURFACE_WSI]', '[Q3PW_SURFACE_PROBE]')):
            continue
        prefix = PREFIX.match(line)
        if not prefix:
            invalid += 1
            continue
        stamp = float(prefix[1])
        if int(prefix[2]) != pid or not start <= stamp <= end:
            continue
        if '[Q3PW_SURFACE_WSI]' in line:
            m = WSI.search(line)
            if not m:
                invalid += 1
                continue
            vendor, device, family, width, height, images, fmt, usage, mx, my, lo, hi = m.groups()
            row = {'vendor_id': vendor, 'device_id': device, 'queue_family': int(family),
                   'extent': [int(width), int(height)], 'images': int(images), 'format': int(fmt),
                   'supported_usage': usage, 'max_extent': [int(mx), int(my)],
                   'min_images': int(lo), 'max_images': int(hi)}
            if not (0 < int(width) <= 256 and 0 < int(height) <= 256 and
                    0 < int(images) <= 32 and int(lo) <= int(images) and
                    (int(hi) == 0 or int(images) <= int(hi))):
                invalid += 1
            created.append((stamp, row))
        else:
            m = DONE.search(line)
            if m:
                completed.append((stamp, int(m[1])))
            else:
                failed.append(line[line.index('[Q3PW_SURFACE_PROBE]'):])
    # Reject partial, duplicated, reordered and mixed failure/success evidence.
    passed = (invalid == 0 and not failed and len(created) == len(completed) == 1
              and created[0][0] <= completed[0][0] and completed[0][1] == 0)
    status = 'creation_verified' if passed else ('no_probe_records' if not
        (created or completed or failed or invalid) else 'unverified_or_failed')
    return {'status': status, 'invalid_records': invalid,
            'native_result': completed[0][1] if len(completed) == 1 else None,
            'wsi': created[0][1] if passed else None,
            'failure_records': failed, 'performance_acceptance': False,
            'scope': 'One process-specific creation/destruction report pair. No image submission, presentation, pose pairing, leak measurement or performance acceptance.'}


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('log', type=Path)
    args.add_argument('--start', required=True, type=float)
    args.add_argument('--end', required=True, type=float)
    args.add_argument('--pid', required=True, type=int)
    a = args.parse_args()
    result = parse(a.log.read_text(encoding='utf-8', errors='replace').splitlines(), a.start, a.end, a.pid)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'creation_verified' else 2


if __name__ == '__main__':
    raise SystemExit(main())
