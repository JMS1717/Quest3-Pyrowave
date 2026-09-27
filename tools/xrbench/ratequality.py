"""Rate-quality curve for PyroWave: what bitrate does this content actually need?

We have been running at ALVR's 600 Mbps because that is what the H.264 path used, not because
anything measured said so. This answers it directly -- encode one real frame at many rates, decode
each, and score it against the source.

The measurement is deliberately offline. A streaming run per bitrate would cost a headset session
each, and the headset drains faster than it charges while streaming; encoding the same captured
frame at twenty rates costs minutes and no battery. The frame comes from `ALVR_PYROWAVE_DUMP`,
which writes the exact planes the encoder consumed, so this is the codec's rate-quality curve on
real content rather than on a test pattern.

Run it where pyrowave-encode, pyrowave-decode and ffmpeg live (the PC):

    python -m xrbench.ratequality <source.y4m> --out curve.csv
"""
import csv
import os
import re
import subprocess
import sys
from pathlib import Path

# Rates worth knowing about: from "obviously too low" up past what ALVR currently sends, so the
# curve shows both the knee and the plateau rather than a slice of one side.
DEFAULT_RATES_MBPS = (10, 25, 50, 75, 100, 150, 200, 300, 400, 600, 800)

DEFAULT_FPS = 72


def bytes_per_frame(bitrate_mbps, fps=DEFAULT_FPS):
    """PyroWave's rate control is a hard per-frame byte cap, not an average.

    That suits an intra-only codec -- every frame is independent, so there is no rate buffer to
    drain and no reason to let one frame borrow from the next.
    """
    if bitrate_mbps <= 0 or fps <= 0:
        raise ValueError(f"bad rate {bitrate_mbps} Mbps at {fps} fps")
    return int(bitrate_mbps * 1_000_000 / 8 / fps)


def parse_psnr(output):
    """Pull the per-plane PSNR out of ffmpeg's psnr filter output.

    Returns None when no psnr line is present, so a failed encode or decode reads as missing
    rather than as zero -- a zero would plot as a real (terrible) measurement.
    """
    match = re.search(
        r"psnr_y:([0-9.inf]+)\s+psnr_u:([0-9.inf]+)\s+psnr_v:([0-9.inf]+)", output
    )
    if not match:
        return None

    def value(text):
        return float("inf") if text.startswith("inf") else float(text)

    return {"psnr_y": value(match.group(1)),
            "psnr_u": value(match.group(2)),
            "psnr_v": value(match.group(3))}


def declared_colour_range(y4m_path):
    """FULL, LIMITED, or None if the header does not say.

    This matters more than it looks. ffmpeg's psnr filter silently inserts a range conversion when
    its two inputs disagree, and a y4m without XCOLORRANGE reads as limited to every tool. That
    is worth about 29 dB of luma and looks exactly like a real codec fault -- lifted blacks and
    banding in the shadows. Checked rather than assumed.
    """
    with open(y4m_path, "rb") as handle:
        header = handle.readline().decode("ascii", "replace")
    if "XCOLORRANGE=FULL" in header:
        return "FULL"
    if "XCOLORRANGE=LIMITED" in header:
        return "LIMITED"
    return None


def sweep(source, rates_mbps=DEFAULT_RATES_MBPS, fps=DEFAULT_FPS, tools=None, workdir=None):
    """Encode/decode/score `source` at each rate. Returns a row per rate, in order."""
    tools = tools or {}
    encode = tools.get("encode", "pyrowave-encode.exe")
    decode = tools.get("decode", "pyrowave-decode.exe")
    ffmpeg = tools.get("ffmpeg", "ffmpeg")
    workdir = Path(workdir or Path(source).parent)

    if declared_colour_range(source) is None:
        raise ValueError(
            f"{source} does not declare XCOLORRANGE. Every tool will read it as limited range "
            f"while the encoder writes full, and ffmpeg will quietly convert between them -- "
            f"about 29 dB of luma, looking just like a codec fault. Re-capture with a dump that "
            f"declares the range."
        )

    rows = []
    for rate in rates_mbps:
        cap = bytes_per_frame(rate, fps)
        wave = workdir / f"rq_{rate}.wave"
        decoded = workdir / f"rq_{rate}.y4m"

        subprocess.run([encode, str(source), str(wave), str(cap)],
                       capture_output=True, check=False)
        if not wave.exists() or wave.stat().st_size == 0:
            rows.append({"mbps": rate, "cap_bytes": cap, "actual_bytes": 0,
                         "psnr_y": None, "psnr_u": None, "psnr_v": None})
            continue

        subprocess.run([decode, str(wave), str(decoded)], capture_output=True, check=False)
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(decoded), "-i", str(source),
             "-lavfi", "psnr=stats_file=-", "-f", "null", "-"],
            capture_output=True, text=True, check=False)
        scores = parse_psnr(result.stdout + result.stderr) or {}

        # The payload is the container minus its 8-byte magic, 32-byte header and 4-byte length.
        actual = max(wave.stat().st_size - 44, 0)
        row = {"mbps": rate, "cap_bytes": cap, "actual_bytes": actual}
        row.update({k: scores.get(k) for k in ("psnr_y", "psnr_u", "psnr_v")})
        rows.append(row)

        for path in (wave, decoded):
            try:
                os.remove(path)
            except OSError:
                pass

    return rows


def format_table(rows, fps=DEFAULT_FPS):
    lines = [f"{'Mbps':>6} {'cap B/frame':>12} {'actual':>10} {'fill':>6} "
             f"{'psnr_y':>8} {'psnr_u':>8} {'psnr_v':>8}"]
    for row in rows:
        fill = f"{100.0 * row['actual_bytes'] / row['cap_bytes']:.0f}%" if row["cap_bytes"] else "-"
        def fmt(key):
            value = row.get(key)
            return "  -  " if value is None else f"{value:8.2f}"
        lines.append(f"{row['mbps']:>6} {row['cap_bytes']:>12} {row['actual_bytes']:>10} "
                     f"{fill:>6} {fmt('psnr_y')} {fmt('psnr_u')} {fmt('psnr_v')}")
    return "\n".join(lines)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="the encoder-input dump (ALVR_PYROWAVE_DUMP)")
    parser.add_argument("--out", help="write the rows to this CSV")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--rates", type=int, nargs="*", default=list(DEFAULT_RATES_MBPS),
                        help="bitrates in Mbps")
    parser.add_argument("--encode", default=r"pyrowave-pc\build-pc\Release\pyrowave-encode.exe")
    parser.add_argument("--decode", default=r"pyrowave-pc\build-pc\Release\pyrowave-decode.exe")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args(argv)

    print(f"source: {args.source}  range={declared_colour_range(args.source)}  {args.fps} fps")
    rows = sweep(args.source, args.rates, args.fps,
                 tools={"encode": args.encode, "decode": args.decode, "ffmpeg": args.ffmpeg})
    print(format_table(rows, args.fps))

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
