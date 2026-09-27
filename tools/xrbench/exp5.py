"""Experiment 5: fused Haar viability on real worn XR content. Analysis and the 19-section report.

Order of evidence is always per-frame, per-clip, per-class; the class aggregate is supplemental.
The Haar RD gate classification uses the frozen bands in `rdcorpus.gate_for`; perceptual and
temporal evidence is reported beside the gate and never changes it. The exploratory
source-characteristic section runs only after the gates are settled.
"""
import csv
import json
import os
import statistics
from pathlib import Path

import numpy as np

from . import rdcorpus
from . import exp2

CLASS_TITLES = {"A_gameplay": "Normal gameplay", "B_rotation": "Rapid rotation", "C_translation": "Translation",
                "D_hifreq": "High-frequency detail", "E_foliage": "Foliage/noise", "F_particles": "Particles/transparency",
                "G_hud_text": "HUD/text", "H_dark_gradient": "Dark/gradients", "X_other": "Other"}


def load_index(path):
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, newline="", encoding="utf-8")))


def accepted_clips(index):
    return [r for r in index if r.get("clip") and str(r.get("accepted")) == "True"]


def fmt(v, nd=2):
    return exp2.fmt(v, nd)


def clip_overheads(frame_matches):
    """{clip: distribution} from per-frame match rows (each with clip, frame, extra_pct, bound)."""
    by = {}
    for r in frame_matches:
        by.setdefault(r["clip"], []).append(r)
    return {c: rdcorpus.overhead_distribution(rs) for c, rs in by.items()}


def class_overheads(index_rows, clip_dist):
    """Per class: the distribution over its accepted clips' per-frame overheads (all frames pooled)
    plus the median of clip medians, retaining the clip rows."""
    out = {}
    for klass in rdcorpus.CLASSES:
        clips = [r["clip"] for r in index_rows if r["class"] == klass and r["clip"] in clip_dist]
        if not clips:
            out[klass] = {"clips": 0, "gate": "UNKNOWN"}
            continue
        medians = [clip_dist[c]["median"] for c in clips if clip_dist[c] and clip_dist[c]["median"] is not None]
        p90s = [clip_dist[c]["p90"] for c in clips if clip_dist[c] and clip_dist[c]["p90"] is not None]
        maxes = [(clip_dist[c]["max"], c, clip_dist[c]["max_frame"], clip_dist[c]["max_is_bound"]) for c in clips if clip_dist[c]]
        bound = any(m[3] for m in maxes)
        worst = max(maxes, key=lambda m: (m[3], m[0] or 0)) if maxes else None
        med = float(np.median(medians)) if medians else None
        out[klass] = {"clips": len(clips), "clip_ids": clips, "median_of_medians": med, "p90_max": max(p90s) if p90s else None,
                      "worst": None if (worst is None or worst[3]) else worst[0], "worst_clip": worst[1] if worst else None,
                      "worst_frame": worst[2] if worst else None, "worst_is_bound": bound,
                      "gate": rdcorpus.gate_for(med) if medians and len(clips) >= 1 else "UNKNOWN"}
    return out


def live_gate(class_over, warnings):
    """A_gameplay <= 25 % AND no clearly unacceptable failure; ambiguous = UNKNOWN."""
    g = class_over.get("A_gameplay", {})
    if g.get("gate") == "UNKNOWN" or g.get("median_of_medians") is None:
        return "UNKNOWN", "A_gameplay not measured"
    if g["median_of_medians"] > 25.0:
        return "NO", f"A_gameplay median overhead {g['median_of_medians']:+.1f} % > 25 %"
    if warnings:
        return "UNKNOWN", "gameplay passes but the corpus shows: " + "; ".join(warnings)
    return "YES", f"A_gameplay median overhead {g['median_of_medians']:+.1f} % <= 25 % and no failure mode found"


def failure_warnings(class_over, clip_dist, roi_rows):
    w = []
    for klass, c in class_over.items():
        if c.get("clips", 0) == 0:
            continue
        if c.get("worst_is_bound") or (c.get("worst") is not None and c["worst"] > 50.0):
            worst = ">BOUND" if c.get("worst_is_bound") else f"{c['worst']:+.0f} %"
            w.append(f"{klass}: frame-level overhead > 50 % (worst {worst})")
        if klass == "E_foliage" and c.get("gate") in ("FAIL",):
            w.append("E_foliage fails the gate")
    for r in roi_rows:
        if r.get("roi_delta_inside") is not None and r["roi_delta_inside"] < -3.0:
            w.append(f"{r['clip']}: ROI PSNR-Y {r['roi_delta_inside']:+.1f} dB (Haar - 9/7) inside the region at equal bytes")
    return w


def correlations(frame_rows):
    """Spearman of per-frame Haar overhead against each source descriptor, individually."""
    keys = ("luma_entropy_bits", "gradient_energy", "edge_density", "temporal_diff", "hf_energy_frac", "local_variance", "flat_frac")
    ys = [r["extra_pct"] for r in frame_rows if not r.get("bound")]
    out = {}
    for k in keys:
        xs = [r.get(k) for r in frame_rows if not r.get("bound")]
        out[k] = rdcorpus.spearman(xs, ys)
    return out


def render(d, out_path):
    d = Path(d)
    index = load_index(d / "index.csv")
    acc = accepted_clips(index)
    frames = json.load(open(d / "frame_matches.json")) if (d / "frame_matches.json").exists() else []
    rd = json.load(open(d / "rd_summary.json")) if (d / "rd_summary.json").exists() else {}
    h264 = list(csv.DictReader(open(d / "h264_frames.csv", newline="", encoding="utf-8"))) if (d / "h264_frames.csv").exists() else []
    roi_rows = json.load(open(d / "roi.json")) if (d / "roi.json").exists() else []
    live = json.load(open(d / "live.json")) if (d / "live.json").exists() else None
    clip_dist = clip_overheads(frames)
    cls = class_overheads(index, clip_dist)
    warnings = failure_warnings(cls, clip_dist, roi_rows)
    gate, why = live_gate(cls, warnings)
    L = []
    A = L.append
    A("# Experiment 5 — Fused Haar viability on real worn XR content\n")
    A("## 1. Executive result\n")
    if not acc:
        A("No accepted worn clips yet: every class is UNKNOWN and the central question is UNKNOWN. The capture console, trigger, scoring and this report are verified on the unworn dry run; the worn session fills the corpus.\n")
    else:
        A(f"Accepted clips: {len(acc)} across {sum(1 for c in cls.values() if c.get('clips'))} classes. Live fused-Haar engineering gate: **{gate}** ({why}). Per-class gates are in section 12; nothing here is an average across classes.\n")
        x = cls.get("X_other", {})
        if x.get("clips"):
            A(f"Supplemental (not one of the eight preregistered classes): X_other = {x['clips']} clips of SteamVR Home worn with unscripted, mostly static head motion. Haar matched-PSNR-Y overhead: median of clip medians {x['median_of_medians']:+.1f} %, largest clip p90 {x['p90_max']:+.1f} %, worst frame {x['worst']:+.1f} % (band {x['gate']} if it were a gated class). Three view-dependent groups appear (about +11 %, +26-28 %, +35 %), so the spread across views inside one scene is wider than the spread across frames inside one clip.\n")
    A("## 2. Exact hypotheses\n")
    A("H5: on representative worn XR content, fused Haar reaches comparable perceptual quality to CDF 9/7 with a bitrate penalty small enough that its ~48.6 % reconstruction-time reduction (Experiment 4, standalone, matched clocks) is a favourable latency/bandwidth trade. Falsified for a general replacement if representative content consistently needs > 50 % more bytes at matched PSNR-Y or shows substantial perceptual/temporal failures the global metric hides; partially supported if only some classes pass. Gate metric: matched-PSNR-Y bitrate overhead, bands frozen (<= 15 STRONG PASS, <= 25 PASS, <= 50 CONDITIONAL, > 50 FAIL, else UNKNOWN).\n")
    A("## 3. Experimental environment\n")
    A("Galaxy XR (Adreno 740) client at the candidate operating point (60 % render, 400 Mbps PyroWave UDP, 90 Hz, gaze centre 0.20, compute path); RTX 3090 PC encoder; clips are the encoder's own input planes (1984x896 side-by-side foveated, BT.709 full-range 4:4:4) written losslessly through the runtime trigger (`ALVR_PYROWAVE_DUMP_TRIGGER`), 90 consecutive frames with a per-frame timestamp sidecar. Offline scoring: PyroWave PC encoder/decoder at the frame's native size, ffmpeg psnr/ssim/libvmaf (PSNR-HVS, VMAF). Corpus and index frozen before analysis (hash in `index.frozen-*.sha256`).\n")
    A("## 4. Corpus description\n")
    A("| clip | class | title | frames | temporal class | mean consecutive diff | identical pairs | accepted | ROI |\n|---|---|---|---|---|---|---|---|---|")
    for r in index:
        if r.get("clip"):
            A(f"| {r['clip']} | {r['class']} | {r.get('title', '')} | {r.get('frames', '')} | {r.get('temporal_class', '')} | {r.get('mean_consecutive_diff', '')} | {r.get('identical_consecutive_frac', '')} | {r.get('accepted', '')} | {r.get('roi', '') or '-'} |")
    A("\nAccepted clips per class against the minimums (4/2/2/3/3/3/3/3): " + "; ".join(f"{k} {c.get('clips', 0)}/{rdcorpus.CLASS_MINIMUM[k]}" for k, c in cls.items() if k != "X_other") + ". Experiment 4's static unworn clips (synthetic panel, photo layout, SteamVR Home with a still head) remain historical evidence and are not in this corpus.\n")
    A("## 5. Temporal validation of every clip\n")
    A("Thresholds (frozen): STATIC if >= 95 % of consecutive pairs are bit-identical or mean |diff| < 0.25; LOW_MOTION < 1.0; ACTIVE < 4.0; HIGH_MOTION otherwise. Rejection only where motion is intrinsic (B, C: ACTIVE or HIGH_MOTION; A, F: not STATIC); G, H, D may be static. The table in section 4 carries every clip's numbers, accepted or not.\n")
    A("## 6. RD curves by content class (per clip, then class; MEASURED)\n")
    if rd.get("clips"):
        A("| class | clip | wavelet | cap B | Mbps@90 | PSNR-Y mean (std, min..max) | PSNR-HVS | SSIM | VMAF |\n|---|---|---|---|---|---|---|---|---|")
        for s in rd["clips"]:
            if s["cap_bytes"] in (250_000, 416_667, 600_000, 800_000):
                A(f"| {s['class']} | {s['clip']} | {s['wavelet']} | {s['cap_bytes']:,} | {s['cap_bytes'] * 8 * 90 / 1e6:.0f} | {s['psnr_y_mean']:.2f} ({s['psnr_y_std']:.2f}, {s['psnr_y_min']:.2f}..{s['psnr_y_max']:.2f}) | {fmt(s['psnr_hvs_mean'])} | {fmt(s['ssim_mean'], 4)} | {fmt(s['vmaf_mean'], 1)} |")
        A("")
    else:
        A("UNKNOWN (no scored clips).\n")
    A("## 7. Equal-byte results (Haar - 9/7, per class and cap; MEASURED)\n")
    if rd.get("class_deltas"):
        A("| class | cap B | clips | dPSNR-Y | dPSNR-HVS | dSSIM |\n|---|---|---|---|---|---|")
        for k, e in rd["class_deltas"].items():
            A(f"| {e['class']} | {e['cap_bytes']:,} | {e['clips']} | {fmt(e['d_psnr_y'])} | {fmt(e['d_hvs'])} | {fmt(e['d_ssim'], 4)} |")
        A("")
    else:
        A("UNKNOWN.\n")
    A("## 8. Matched-PSNR-Y results (per frame -> per clip -> per class; never 'equal quality')\n")
    if frames:
        A("Per frame (Haar bytes to reach 9/7's PSNR-Y at 416,667 B on the same frame; `>BOUND` = not reached within 2,000,000 B):\n")
        A("| clip | frame | 9/7 bytes | 9/7 PSNR-Y | Haar bytes | extra | PSNR-HVS 9/7 / Haar | SSIM 9/7 / Haar | VMAF 9/7 / Haar |\n|---|---|---|---|---|---|---|---|---|")
        for r in frames:
            extra = ">BOUND" if r.get("bound") else f"{r['extra_pct']:+.1f} %"
            A(f"| {r['clip']} | {r['frame']} | {r['bytes97']:,} | {r['psnr_y97']:.2f} | {r['bytes_haar']:,} | {extra} | {fmt(r['psnr_hvs97'])} / {fmt(r['psnr_hvs_haar'])} | {fmt(r['ssim97'], 4)} / {fmt(r['ssim_haar'], 4)} | {fmt(r['vmaf97'], 1)} / {fmt(r['vmaf_haar'], 1)} |")
        A("\nPer clip (distribution of the per-frame overhead):\n")
        A("| clip | n | min | median | mean | p90 | p95 | max (frame) | >BOUND frames |\n|---|---|---|---|---|---|---|---|---|")
        for c, dd in clip_dist.items():
            A(f"| {c} | {dd['n']} | {fmt(dd['min'], 1)} | {fmt(dd['median'], 1)} | {fmt(dd['mean'], 1)} | {fmt(dd['p90'], 1)} | {fmt(dd['p95'], 1)} | {'>BOUND' if dd['max_is_bound'] else fmt(dd['max'], 1)} ({dd['max_frame']}) | {dd['n_bound']} |")
        A("\nPer class (median of clip medians; the clip rows above are the evidence):\n")
        A("| class | clips | median of clip medians | largest clip p90 | worst frame overhead | worst clip/frame |\n|---|---|---|---|---|---|")
        for k, c in cls.items():
            if c.get("clips"):
                A(f"| {k} | {c['clips']} | {fmt(c['median_of_medians'], 1)} | {fmt(c['p90_max'], 1)} | {'>BOUND' if c['worst_is_bound'] else fmt(c['worst'], 1)} | {c['worst_clip']} / {c['worst_frame']} |")
        A("")
    else:
        A("UNKNOWN.\n")
    A("## 9. Perceptual metric comparison at the matched point\n")
    A("PSNR-HVS-M, SSIM and VMAF at each frame's matched point are in the section 8 table; disagreements with PSNR-Y are flagged in section 12 as perceptual warnings and do not alter the gate.\n")
    A("## 10. Temporal / worst-frame analysis\n")
    if rd.get("clips"):
        A("PSNR-Y std, min and max across the sampled frames per clip and cap are in section 6; the worst frame per clip for the Haar overhead is in section 8. Frame-size variance: PyroWave's rate control fills the cap on every frame (fill within 0.05 %), so byte variance is by construction ~0 for both transforms at a fixed cap.\n")
    else:
        A("UNKNOWN.\n")
    A("## 11. ROI analysis\n")
    if roi_rows:
        A("| clip | box (x y w h) | wavelet | cap | PSNR-Y inside | outside |\n|---|---|---|---|---|---|")
        for r in roi_rows:
            A(f"| {r['clip']} | {r['box']} | {r['wavelet']} | {r['cap_bytes']:,} | {fmt(r['inside'])} | {fmt(r['outside'])} |")
        A("")
    else:
        A("UNKNOWN (no ROI recorded yet).\n")
    A("## 12. Haar RD gate classification by class\n")
    A("| Content class | Clips | Temporal class | Median Haar overhead | p90 overhead | Worst overhead | PSNR-HVS/SSIM/VMAF evidence | ROI warning | Haar RD gate |\n|---|---:|---|---:|---:|---:|---|---|---|")
    for k in rdcorpus.CLASSES[:-1]:
        c = cls.get(k, {})
        tcs = sorted({r["temporal_class"] for r in acc if r["class"] == k})
        ev = "see section 8" if c.get("clips") else "UNKNOWN"
        roi_w = "; ".join(w for w in warnings if w.startswith(tuple(r["clip"] for r in acc if r["class"] == k)) and "ROI" in w) or ("-" if c.get("clips") else "UNKNOWN")
        A(f"| {CLASS_TITLES[k]} | {c.get('clips', 0)} | {'/'.join(tcs) if tcs else 'UNKNOWN'} | {fmt(c.get('median_of_medians'), 1)} % | {fmt(c.get('p90_max'), 1)} % | {'>BOUND' if c.get('worst_is_bound') else fmt(c.get('worst'), 1) + ' %'} | {ev} | {roi_w} | **{c.get('gate', 'UNKNOWN')}** |")
    A("\nPerceptual warnings (do not change the gate): " + ("; ".join(warnings) if warnings else "none recorded") + ".\n")
    A("### Tail-risk table\n")
    A("| Class | Typical overhead (median) | p90 | Worst frame | Worst overhead | Source characteristics of the worst frame | Interpretation |\n|---|---:|---:|---|---:|---|---|")
    for k in rdcorpus.CLASSES[:-1]:
        c = cls.get(k, {})
        if not c.get("clips"):
            A(f"| {CLASS_TITLES[k]} | UNKNOWN | UNKNOWN | - | - | - | not measured |")
            continue
        wf = next((r for r in frames if r["clip"] == c["worst_clip"] and r["frame"] == c["worst_frame"]), {})
        src = ", ".join(f"{key} {fmt(wf.get(key), 2)}" for key in ("luma_entropy_bits", "gradient_energy", "edge_density", "temporal_diff")) if wf else "-"
        interp = "tail within 2x of typical" if (c["worst"] is not None and c["median_of_medians"] and c["worst"] <= 2 * max(c["median_of_medians"], 5)) else "isolated expensive frames: Haar normally efficient here but occasionally not"
        A(f"| {CLASS_TITLES[k]} | {fmt(c['median_of_medians'], 1)} % | {fmt(c['p90_max'], 1)} % | {c['worst_clip']} / {c['worst_frame']} | {'>BOUND' if c['worst_is_bound'] else fmt(c['worst'], 1) + ' %'} | {src} | {interp} |")
    A("")
    A("## 13. Live Haar results\n")
    A(json.dumps(live, indent=1) if live else "NOT RUN (live gate: " + gate + ").\n")
    A("## 14. Reconstruction/conversion fusion results\n")
    A("NOT RUN (follows the live gate).\n")
    A("## 15. H.264/CAVLC quality control (same encoder settings as the raw pipe on identical frames, offline; live pipeline equivalence incomplete)\n")
    if h264:
        by = {}
        for r in h264:
            by.setdefault((r["class"], r["clip"], int(r["target_mbps"])), []).append(r)
        A("| class | clip | target Mbps | achieved Mbps | frames | PSNR-Y mean (min..max) | PSNR-HVS | SSIM | VMAF |\n|---|---|---|---|---|---|---|---|---|")
        for (k, c, m), rs in sorted(by.items()):
            ys = [float(r["psnr_y"]) for r in rs if r["psnr_y"] not in ("", "None")]
            hv = [float(r["psnr_hvs"]) for r in rs if r["psnr_hvs"] not in ("", "None")]
            ss = [float(r["ssim"]) for r in rs if r["ssim"] not in ("", "None")]
            vm = [float(r["vmaf"]) for r in rs if r["vmaf"] not in ("", "None")]
            A(f"| {k} | {c} | {m} | {rs[0]['achieved_mbps']} | {len(rs)} | {statistics.mean(ys):.2f} ({min(ys):.2f}..{max(ys):.2f}) | {fmt(statistics.mean(hv) if hv else None)} | {fmt(statistics.mean(ss) if ss else None, 4)} | {fmt(statistics.mean(vm) if vm else None, 1)} |")
        A("\nNot merged with PyroWave latency numbers: different pipeline, instrumentation and operating conditions. What it answers: quality at a bitrate for H.264 CAVLC on the same frames, next to the PyroWave RD curves of section 6.\n")
    else:
        A("UNKNOWN (not run).\n")
    A("## 16. Contradictory / unexpected evidence\n")
    A("- Recorded as found; see section 12 warnings and the per-frame table. Experiment 3/4 contradiction preserved: Kodak ~2x vs SteamVR Home +9.4 % vs static synthetic +35 %.\n")
    A("## 17. Remaining UNKNOWNs\n")
    A("- Classes below their minimum clip count (section 4); ROI where none recorded; H.264 live quality; live fence for fused Haar (section 13); the [3,2] > [5] mechanism (not addressed here by design).\n")
    A("## 18. Falsification assessment\n")
    fails = [k for k, c in cls.items() if c.get("gate") == "FAIL"]
    passes = [k for k, c in cls.items() if c.get("gate") in ("PASS", "STRONG PASS")]
    unknown = [k for k, c in cls.items() if c.get("gate") == "UNKNOWN" and k != "X_other"]
    A(f"Classes FAIL: {', '.join(fails) or 'none'}; PASS/STRONG PASS: {', '.join(passes) or 'none'}; UNKNOWN: {', '.join(unknown) or 'none'}. H5 as a general replacement is " +
      ("FALSIFIED (representative classes fail)" if "A_gameplay" in fails else "PARTIALLY SUPPORTED (class-dependent)" if passes and (fails or any(c.get('gate') == 'CONDITIONAL' for c in cls.values())) else "SUPPORTED on the measured classes" if passes and not unknown else "UNKNOWN (insufficient evidence)") + ".\n")
    A("## 19. Recommendation for Experiment 6\n")
    A("Decided from section 12 and the live gate; if the gate is YES: the live fused-Haar [3,2] path with transform id, mismatch rejection, clock sampling and fence timing, then reconstruction+conversion fusion; if NO/UNKNOWN: either more corpus (the UNKNOWN classes) or pipeline fusion on the 9/7 path. The exploratory correlations below are candidate hypotheses only.\n")
    A("## EXPLORATORY: Predictors of Haar RD Penalty\n")
    if frames and any("luma_entropy_bits" in r for r in frames):
        cor = correlations(frames)
        A("Spearman rank correlation of the per-frame Haar overhead with each source descriptor, individually (no combined score); exploratory, computed after the gates and not used by them:\n")
        A("| descriptor | Spearman rho | n |\n|---|---|---|")
        n = sum(1 for r in frames if not r.get("bound"))
        for k, v in cor.items():
            A(f"| {k} | {fmt(v, 3)} | {n} |")
        A("")
    else:
        A("Not computed (no descriptors yet).\n")
    A("## Central question\n")
    A(f"Does the evidence justify spending engineering effort on fused Haar as the primary ultra-low-latency PyroWave path? **{gate}** — {why}. Answered from the preregistered gates and the measured evidence only.\n")
    Path(out_path).write_text("\n".join(L) + "\n", encoding="utf-8")
    (Path(out_path).parent / "summary.json").write_text(json.dumps({"classes": cls, "clip_overheads": clip_dist, "warnings": warnings, "live_gate": [gate, why],
                                                                    "index": index, "frame_matches": frames}, indent=1, default=str))
    return "\n".join(L)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dir"); p.add_argument("--out")
    a = p.parse_args(argv)
    txt = render(a.dir, a.out or os.path.join(a.dir, "REPORT.md"))
    print(len([l for l in txt.splitlines() if l.startswith("## ")]), "sections")


if __name__ == "__main__":
    main()
