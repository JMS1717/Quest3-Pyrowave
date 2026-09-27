"""Pool beta testers' PyroWave test reports into one CSV.

The dashboard's "Export PyroWave Test Report" writes one `pyrowave-report-<id>.json` per 60-second
run. This checks each against the schema and for anything identifying (the same rules the
dashboard sanitizes with), then flattens every accepted report into one row: hardware, link,
settings, decode and fence distributions, fps, latency, thermals and the tester's answers.

    python -m xrbench.beta_reports <folder> [--out pooled.csv]

A rejected report is named with the reason, never with the offending text.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
REQUIRED = ("report_id", "settings", "system", "window", "fps", "motion_to_photon_ms",
            "frame_counts", "headset_start", "headset_end", "subjective")

_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(:\d+)?$")
_MAC = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")
_IPV6 = re.compile(r"^[0-9A-Fa-f:]+$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SERIAL = re.compile(r"^(?=.*\d)(?=.*[A-Z])[A-Z0-9]{8,}$")
_USER_PATHS = ("c:/users/", "/users/", "/home/", "/storage/emulated/", "/sdcard/")
_HOST_SUFFIXES = (".local", ".lan", ".alvr")


def _kind(token):
    token = token.strip(",;()[]{}<>\"'")
    bare = token.rstrip(".")
    lower = token.lower().replace("\\", "/")
    if any(p in lower for p in _USER_PATHS):
        return "user path"
    if _EMAIL.match(bare):
        return "e-mail"
    if _MAC.match(bare):
        return "MAC address"
    m = _IPV4.match(bare)
    if m and all(int(g) <= 255 for g in m.groups()[:4]):
        return "IP address"
    # compressed or full eight-group IPv6; "4:4:4" (chroma) is neither
    if _IPV6.match(bare) and ("::" in bare or bare.count(":") == 7) and re.search(r"[0-9A-Fa-f]", bare):
        return "IP address"
    if bare.lower().endswith(_HOST_SUFFIXES):
        return "hostname"
    if _SERIAL.match(bare):
        return "serial-like token"
    return None


def identifying(text):
    """Kinds of identifying tokens in a string (no tokens returned, only what kind they are)."""
    return [k for k in (_kind(t) for t in str(text).split()) if k]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def validate(report):
    """Problems with a report, empty when it can be pooled."""
    problems = []
    if report.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version is {report.get('schema_version')!r}, expected {SCHEMA_VERSION}")
    problems += [f"missing {key}" for key in REQUIRED if key not in report]
    kinds = sorted({k for s in _strings(report) for k in identifying(s)})
    if kinds:
        problems.append("identifying data: " + ", ".join(kinds))
    return problems


def _get(report, *path):
    node = report
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _size(value):
    return f"{value[0]}x{value[1]}" if isinstance(value, list) and len(value) == 2 else ""


def flatten(report):
    """One CSV row per report; per-frame arrays stay in the JSON."""
    s = report.get("settings", {})
    pyro = s.get("pyrowave") or {}
    fov = s.get("foveated_encoding") or {}
    row = {
        "report_id": report.get("report_id"),
        "gpu": _get(report, "system", "gpu"), "gpu_driver": _get(report, "system", "gpu_driver"),
        "cpu": _get(report, "system", "cpu"), "os": _get(report, "system", "os"),
        "streamer_version": _get(report, "system", "streamer_version"),
        "headset_model": _get(report, "system", "headset_model"),
        "link": _get(report, "subjective", "link"), "band": _get(report, "subjective", "band"),
        "router_model": _get(report, "subjective", "router_model"),
        "profile": s.get("profile"), "codec": s.get("codec"), "chroma": s.get("chroma"),
        "render_scale_percent": s.get("render_scale_percent"),
        "render_per_eye": _size(s.get("render_per_eye")), "encoded": _size(report.get("encoded_size")),
        "refresh_hz": s.get("refresh_hz"), "bitrate_mbps": s.get("bitrate_mbps"),
        "bitrate_mbps_achieved": report.get("bitrate_mbps_achieved"),
        "transport": pyro.get("transport"), "wavelet": pyro.get("wavelet"),
        "decode_path": pyro.get("decode_path"),
        "foveation": fov.get("enabled"), "follow_gaze": fov.get("follow_gaze"),
        "advanced_changes": ";".join(s.get("advanced_changes") or []),
        "frames": _get(report, "window", "frames"),
        "fps_median": _get(report, "fps", "median"), "fps_p1_low": _get(report, "fps", "p1_low"),
    }
    for name, key in (("mtp", "motion_to_photon_ms"), ("decode", "pyrowave_gpu_decode_ms"),
                      ("fence", "pyrowave_fence_ms")):
        for q in ("mean", "p50", "p95", "p99"):
            row[f"{name}_{q}_ms"] = _get(report, key, q)
    for count in ("complete", "partial", "skipped", "dropped", "superseded", "late_packets",
                  "decode_failures"):
        row[count] = _get(report, "frame_counts", count)
    row["packets_lost"] = report.get("packets_lost")
    for end in ("start", "end"):
        row[f"thermal_{end}"] = _get(report, f"headset_{end}", "thermal_status")
        row[f"battery_temp_{end}_c"] = _get(report, f"headset_{end}", "battery_temperature_c")
        row[f"battery_{end}"] = _get(report, f"headset_{end}", "battery_level")
    row["chroma_444_difference"] = _get(report, "subjective", "chroma_444_difference")
    row["sharpness"] = _get(report, "subjective", "sharpness_1_to_5")
    row["smoothness"] = _get(report, "subjective", "smoothness_1_to_5")
    row["notes"] = _get(report, "subjective", "notes")
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", help="folder of pyrowave-report-*.json files")
    parser.add_argument("--out", help="CSV to write (default: <folder>/pooled.csv)")
    args = parser.parse_args(argv)
    folder = Path(args.folder)
    rows, rejected = [], []
    for path in sorted(folder.glob("pyrowave-report-*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            rejected.append((path.name, f"unreadable: {type(e).__name__}"))
            continue
        problems = validate(report)
        if problems:
            rejected.append((path.name, "; ".join(problems)))
        else:
            rows.append(flatten(report))
    out = Path(args.out) if args.out else folder / "pooled.csv"
    if rows:
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"{len(rows)} reports pooled -> {out}, {len(rejected)} rejected")
    for name, reason in rejected:
        print(f"  rejected {name}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
