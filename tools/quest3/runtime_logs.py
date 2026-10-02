"""Select runtime diagnostics from one recorded Android client process.

Startup records can precede capture. These records are context, not continuous
runtime-frequency or optical-frame acceptance.
"""
import math
import re

PREFIX = re.compile(r'^\s*(\d+(?:\.\d+)?)\s+(\d{1,10})\s+\d+\s+[VDIWEF]\s+')
MARKER = re.compile(r'\[Q3PW_(CAPS|PROBE|VERIFIED|RATE|EFFECTIVE)\]')


def parse_pid(value):
    if not isinstance(value, str):
        raise ValueError('Need exactly one running Quest3-Pyrowave client PID')
    text = value.strip()
    if not re.fullmatch(r'[1-9][0-9]*', text) or int(text) >= 2**31:
        raise ValueError('Need exactly one running Quest3-Pyrowave client PID')
    return int(text)


def extract(log, pid):
    if type(pid) is not int or not 0 < pid < 2**31:
        raise ValueError('Need the recorded client PID')
    records = []
    for line in log.splitlines():
        prefix, marker = PREFIX.match(line), MARKER.search(line)
        if not prefix or not marker or int(prefix[2]) != pid:
            continue
        timestamp = float(prefix[1])
        if not math.isfinite(timestamp):
            continue
        records.append({'epoch_s': timestamp, 'message': line[marker.start():]})
    return records


def process_evidence(before, after):
    stable = (type(before) is int and type(after) is int and
              0 < before < 2**31 and before == after)
    return {'pid_before': before, 'pid_after': after,
            'same_pid_during_capture': stable,
            'status': 'same_pid' if stable else 'changed_or_unavailable',
            'scope': 'PID sampled before/after; a reused PID is not a session-generation proof. Runtime startup records can predate capture and do not verify continuous refresh.'}
