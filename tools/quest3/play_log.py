"""Light play-session logger (PLAN 1.7).

    python -m tools.quest3.play_log record OUT_DIR    # until Ctrl+C; run beside a play session
    python -m tools.quest3.play_log report OUT_DIR    # per-minute table of what was recorded
    python -m tools.quest3.play_log replay EVENTS_JSONL OUT_DIR  # a harness phase's server events

`record` reads the server's event stream (the dashboard websocket) and keeps memory flat:

- `seconds.csv`: one row per second. GraphStatistics (one event per frame) are reduced to the
  frame count and per-stage means, plus the p90 and max of the total; the HeadsetTelemetry counters
  become per-second rates; StatisticsSummary values are copied.
- `log.txt`: every log line, with IPv4 addresses replaced. The client forwards its error-level
  lines, so the per-second `[Q3PW_*]` counters of both ends (transport, poses, tracking arrival,
  frame timing, audio) are in it.

Nothing is read from the headset, so the logger adds no adb traffic.
"""
import argparse
import csv
import json
import math
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

URL = 'ws://127.0.0.1:8082/api/events'
IP = re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b')
STAGES = ('game_time_s', 'server_compositor_s', 'encoder_s', 'network_s', 'decoder_s',
          'decoder_queue_s', 'client_compositor_s', 'vsync_queue_s', 'total_pipeline_latency_s')
SUMMARY = ('video_mbits_per_sec', 'packets_lost_per_sec', 'client_fps', 'server_fps')
COUNTERS = ('complete', 'skipped', 'dropped', 'partial', 'superseded', 'decode_failures',
            'completed_eye_copies')
COLUMNS = (['epoch_s', 'frames', 'bytes_mean', 'bytes_max'] + [s[:-2] + '_ms' for s in STAGES] +
           ['total_ms_p90', 'total_ms_max'] + list(SUMMARY) + [c + '_per_s' for c in COUNTERS] +
           ['thermal_status', 'battery_c'])


def ms(value):
    return round(value * 1000, 3) if isinstance(value, (int, float)) else ''


class Second:
    def __init__(self, epoch_s):
        self.epoch_s = epoch_s
        self.frames = 0
        self.bytes = []
        self.sums = defaultdict(float)
        self.totals = []
        self.summary = {}
        self.telemetry = {}

    def graph(self, data):
        self.frames += 1
        self.bytes.append(data.get('video_packet_bytes') or 0)
        for stage in STAGES:
            value = data.get(stage)
            if isinstance(value, (int, float)):
                self.sums[stage] += value
        total = data.get('total_pipeline_latency_s')
        if isinstance(total, (int, float)):
            self.totals.append(total)

    def row(self, rates):
        n = max(self.frames, 1)
        totals = sorted(self.totals)
        p90 = totals[min(len(totals) - 1, int(math.ceil(len(totals) * 0.9)) - 1)] if totals else None
        row = [self.epoch_s, self.frames,
               round(sum(self.bytes) / n) if self.bytes else '', max(self.bytes) if self.bytes else '']
        row += [ms(self.sums[s] / n) if self.frames else '' for s in STAGES]
        row += [ms(p90), ms(totals[-1] if totals else None)]
        row += [self.summary.get(k, '') for k in SUMMARY]
        row += [rates.get(c, '') for c in COUNTERS]
        row += [self.telemetry.get('thermal_status', ''), self.telemetry.get('battery_temperature_c', '')]
        return row


class Recorder:
    """Turns server events into `seconds.csv` rows and `log.txt` lines."""

    def __init__(self, out):
        out.mkdir(parents=True, exist_ok=True)
        seconds_path = out / 'seconds.csv'
        new = not seconds_path.exists()
        self.seconds_file = seconds_path.open('a', newline='', encoding='utf-8')
        self.writer = csv.writer(self.seconds_file)
        if new:
            self.writer.writerow(COLUMNS)
        self.log_file = (out / 'log.txt').open('a', encoding='utf-8')
        self.current, self.last_counters, self.rates = None, None, {}

    def note(self, now, text):
        self.log_file.write(f'{now:.3f} [play_log] {text}\n')
        self.log_file.flush()

    def tick(self, now):
        epoch_s = int(now)
        if self.current is None or epoch_s != self.current.epoch_s:
            self.flush_second()
            self.current = Second(epoch_s)

    def flush_second(self):
        if self.current is not None:
            self.writer.writerow(self.current.row(self.rates))
            self.seconds_file.flush()
            self.log_file.flush()
            self.rates = {}
            self.current = None

    def handle(self, event, now):
        self.tick(now)
        kind = event.get('event_type', {}).get('id')
        data = event.get('event_type', {}).get('data')
        if kind == 'GraphStatistics' and isinstance(data, dict):
            self.current.graph(data)
        elif kind == 'StatisticsSummary' and isinstance(data, dict):
            self.current.summary = data
        elif kind == 'HeadsetTelemetry' and isinstance(data, dict):
            self.current.telemetry = data
            counters = data.get('pyrowave') or {}
            values = {c: counters.get(c) for c in COUNTERS}
            last = self.last_counters
            if last is not None:
                dt = max(now - last[0], 1e-3)
                self.rates = {c: round((v - last[1][c]) / dt, 1) for c, v in values.items()
                              if isinstance(v, (int, float)) and isinstance(last[1].get(c), (int, float))
                              and v >= last[1][c]}
            self.last_counters = (now, values)
        elif kind == 'Log' and isinstance(data, dict):
            text = IP.sub('<ip>', str(data.get('content', '')))
            self.log_file.write(f'{now:.3f} {str(data.get("severity", ""))[:1]} {text}\n')
        elif kind:
            self.log_file.write(f'{now:.3f} [{kind}] {IP.sub("<ip>", json.dumps(data)[:500])}\n')

    def close(self, now):
        self.flush_second()
        self.note(now, 'stop')
        self.seconds_file.close()
        self.log_file.close()


def record(out):
    import websocket  # websocket-client

    recorder = Recorder(out)
    recorder.note(time.time(), 'start')
    while True:
        try:
            ws = websocket.create_connection(URL, header=['X-ALVR: true'], timeout=5)
            recorder.note(time.time(), 'connected')
            while True:
                try:
                    recorder.handle(json.loads(ws.recv()), time.time())
                except websocket.WebSocketTimeoutException:
                    recorder.tick(time.time())
        except KeyboardInterrupt:
            break
        except Exception as error:  # the server restarted: reconnect
            recorder.note(time.time(), f'reconnect after {type(error).__name__}')
            recorder.last_counters = None
            try:
                time.sleep(2)
            except KeyboardInterrupt:
                break
    recorder.close(time.time())


def replay(events_jsonl, out):
    """Feeds a harness phase's events.jsonl (lines of {recv_epoch, event}) through the recorder."""
    recorder = Recorder(out)
    now = 0.0
    with events_jsonl.open(encoding='utf-8') as f:
        for line in f:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            now = float(item.get('recv_epoch', now))
            recorder.handle(item.get('event', {}), now)
    recorder.close(now)

TAG = re.compile(r'\[(Q3PW_\w+)\](.*)')
PAIR = re.compile(r'(\w+)=(-?\d+(?:\.\d+)?)\b')


def pct(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def fmt(value, digits=1):
    return '' if value is None else f'{value:.{digits}f}'


def report(out, minutes):
    rows = defaultdict(lambda: defaultdict(list))
    with (out / 'seconds.csv').open(encoding='utf-8') as f:
        for r in csv.DictReader(f):
            minute = int(float(r['epoch_s'])) // 60
            for key in ('completed_eye_copies_per_s', 'total_pipeline_latency_ms', 'vsync_queue_ms',
                        'network_ms', 'decoder_ms', 'bytes_mean', 'dropped_per_s', 'frames'):
                if r.get(key) not in ('', None):
                    rows[minute][key].append(float(r[key]))
    with (out / 'log.txt').open(encoding='utf-8', errors='replace') as f:
        for line in f:
            m = TAG.search(line)
            if not m:
                continue
            try:
                minute = int(float(line.split(' ', 1)[0])) // 60
            except ValueError:
                continue
            tag, kv = m.group(1), {k: float(v) for k, v in PAIR.findall(m.group(2))}
            bucket = rows[minute]
            if tag == 'Q3PW_UDP_SEND':  # Wi-Fi only: frames sent with the previous frame's pose
                bucket['udp_frames'].append(kv.get('frames', 0))
                bucket['repeated'].append(kv.get('repeated_timestamps', 0))
            elif tag == 'Q3PW_PRESENT' and 'timing_new' in kv:
                # Presents carrying a new game frame (`.81`+), counted per second. The once-a-second
                # history count in [Q3PW_FRAMETIMING] undercounts above about 120 fps.
                bucket['game_frames'].append(kv['timing_new'])
            elif tag == 'Q3PW_POSE_EXTRAPOLATE':
                bucket['extrapolated'].append(kv.get('extrapolated', 0))
                bucket['vsyncs'].append(kv.get('vsyncs', 0))
            elif tag == 'Q3PW_TRACKING_RX':
                bucket['tracking_gap_max'].append(kv.get('max', 0))
            elif tag == 'Q3PW_TRANSPORT':
                bucket['transport_dropped'].append(kv.get('dropped', 0))
                bucket['transport_missing_kb'].append(kv.get('missing_kb', 0))
            elif tag == 'Q3PW_AUDIO':
                bucket['audio_silent'].append(kv.get('silent_batches', 0))
                bucket['audio_gap_max'].append(kv.get('gap_ms_max', 0))
    head = ('minute (UTC)', 'fresh p50', 'fresh p10', 'game fps', 'repeat %', 'extrap %', 'track gap max',
            'latency', 'vsync q', 'net', 'decode', 'KB/frame', 'video drops', 'audio silent',
            'audio gap max')
    print(' | '.join(head))
    for minute in sorted(rows)[-minutes:] if minutes else sorted(rows):
        b = rows[minute]
        udp_frames = sum(b['udp_frames'])
        vsyncs = sum(b['vsyncs'])
        game = pct(b['game_frames'], 0.5)
        cells = (
            time.strftime('%H:%M', time.gmtime(minute * 60)),
            fmt(pct(b['completed_eye_copies_per_s'], 0.5)), fmt(pct(b['completed_eye_copies_per_s'], 0.1)),
            fmt(game, 0),
            fmt(100 * sum(b['repeated']) / udp_frames if udp_frames else None, 2),
            fmt(100 * sum(b['extrapolated']) / vsyncs if vsyncs else None, 2),
            fmt(max(b['tracking_gap_max']) if b['tracking_gap_max'] else None),
            fmt(pct(b['total_pipeline_latency_ms'], 0.5)), fmt(pct(b['vsync_queue_ms'], 0.5)),
            fmt(pct(b['network_ms'], 0.5)), fmt(pct(b['decoder_ms'], 0.5)),
            fmt(pct(b['bytes_mean'], 0.5) / 1024 if b['bytes_mean'] else None, 0),
            # [Q3PW_TRANSPORT] counts are totals since the client connected.
            fmt(max(b['transport_dropped']) - min(b['transport_dropped']) if b['transport_dropped'] else None, 0),
            fmt(sum(b['audio_silent']) if b['audio_silent'] else None, 0),
            fmt(max(b['audio_gap_max']) if b['audio_gap_max'] else None, 0),
        )
        print(' | '.join(cells))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('record').add_argument('out', type=Path)
    rp = sub.add_parser('replay', help="a harness phase's events.jsonl into OUT")
    rp.add_argument('events', type=Path)
    rp.add_argument('out', type=Path)
    rep = sub.add_parser('report')
    rep.add_argument('out', type=Path)
    rep.add_argument('--minutes', type=int, default=0, help='only the last N minutes')
    args = parser.parse_args(argv)
    if args.command == 'record':
        record(args.out)
    elif args.command == 'replay':
        replay(args.events, args.out)
    else:
        report(args.out, args.minutes)


if __name__ == '__main__':
    sys.exit(main())
