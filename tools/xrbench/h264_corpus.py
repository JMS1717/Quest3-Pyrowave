"""Experiment 5 H.264/CAVLC quality-at-bitrate control on the same corpus clips: ffmpeg's h264_nvenc
with the raw pipe's settings (p1, ull, cbr, cavlc, bf 0, GOP 90, forced IDR, zerolatency), encoded
as a whole clip so CBR and the GOP behave as in the live pipe, decoded with ffmpeg, and the same
sampled frames scored with the same pipeline. Labelled: same encoder settings as the raw pipe on
identical frames, offline; live pipeline equivalence incomplete. Achieved bitrate is measured
from the bitstream, never assumed."""
import json
import os
import subprocess
from pathlib import Path

from . import rdcorpus
from . import rdmatrix

MBPS = (180, 300, 432, 600)


def nvenc_args(mbps, fps=90, gop=90, coder="cavlc"):
    """The raw pipe's encoder settings (tools/xrbench/rawpipe.py:149-163) for a y4m input."""
    return ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-rc", "cbr", "-coder", coder,
            "-b:v", f"{mbps}M", "-maxrate", f"{mbps}M", "-bufsize", f"{int(mbps * 1000 / fps)}k",
            "-zerolatency", "1", "-delay", "0", "-bf", "0", "-g", str(gop), "-forced-idr", "1", "-aud", "1"]


def encode_clip(ffmpeg, clip_y4m, mbps, out_h264, fps=90):
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_y4m)] + nvenc_args(mbps, fps) + ["-f", "h264", str(out_h264)]
    subprocess.run(cmd, check=True, capture_output=True)
    return os.path.getsize(out_h264)


def decode_clip(ffmpeg, h264, out_y4m):
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(h264), "-pix_fmt", "yuv444p", "-color_range", "pc", "-f", "yuv4mpegpipe", "-strict", "-1", str(out_y4m)],
                   check=True, capture_output=True)


def achieved_mbps(size_bytes, frames, fps=90):
    return size_bytes * 8 * fps / frames / 1e6


def score_clip_h264(clip_y4m, klass, workdir, ffmpeg="ffmpeg", every=10, mbps_list=MBPS, fps=90, log=print):
    """Per sampled frame and bitrate: achieved Mbps, PSNR-Y, PSNR-HVS, SSIM, VMAF against the source frame."""
    workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    n = rdcorpus.clip_frames(clip_y4m)
    rows = []
    for mbps in mbps_list:
        h264 = workdir / f"clip_{mbps}.h264"
        size = encode_clip(ffmpeg, clip_y4m, mbps, h264, fps)
        dec = workdir / f"dec_{mbps}.y4m"
        decode_clip(ffmpeg, h264, dec)
        got = achieved_mbps(size, n, fps)
        for fi in range(0, n, every):
            src = rdcorpus.extract_frame(clip_y4m, fi, workdir / "src.y4m")
            d = rdcorpus.extract_frame(dec, fi, workdir / "d.y4m")
            s = rdmatrix.score(ffmpeg, d, src, workdir)
            row = {"class": klass, "clip": Path(clip_y4m).stem, "frame": fi, "codec": "h264_nvenc_cavlc", "target_mbps": mbps,
                   "achieved_mbps": round(got, 1), "bytes_per_frame_mean": size / n, "psnr_y": s.get("psnr_y"), "psnr_hvs": s.get("psnr_hvs"),
                   "ssim": s.get("ssim_all"), "vmaf": s.get("vmaf")}
            rows.append(row)
            log(f"{klass} {Path(clip_y4m).stem} f{fi} h264 {mbps} Mbps (got {got:.0f}): psnr_y {row['psnr_y']}")
    return rows


def main(argv=None):
    import argparse, csv
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", required=True); p.add_argument("--out", required=True); p.add_argument("--every", type=int, default=10)
    p.add_argument("--index", default=None, help="corpus index.csv; only accepted clips are scored when given")
    a = p.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    accepted = None
    if a.index:
        accepted = {r["clip"] for r in csv.DictReader(open(a.index, newline="", encoding="utf-8")) if str(r.get("accepted")) == "True"}
    rows = []
    for klass_dir in sorted(Path(a.corpus).iterdir()):
        if not klass_dir.is_dir() or klass_dir.name.startswith("_"):
            continue
        for clip in sorted(klass_dir.glob("*.y4m")):
            if accepted is not None and clip.stem not in accepted:
                continue
            rows += score_clip_h264(clip, klass_dir.name, out / "work", every=a.every, log=lambda s: print(s, flush=True))
            with open(out / "h264_frames.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("done", flush=True)


if __name__ == "__main__":
    main()
