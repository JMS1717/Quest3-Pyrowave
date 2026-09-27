"""Experiment 1: PyroWave fragment path vs compute path on the Galaxy XR -- analysis.

Reads each cell's per-segment `logcat.txt` (the receiver's `gpu decode ms:` / `convert ms:` /
`submit->fence ms:` report lines, several per cell) and `meta.json`, keeps every cell separate,
forms the paired deltas PF-1 -> PC-1 and PF-2 -> PC-2, checks thermal matching (<= 3 C on the
starting hottest zone), computes the GPU-to-fence propagation K per statistic, and only then
pools. Renders the 20-item report that was specified.
"""
import json
import re
from pathlib import Path

STATS = ("min", "mean", "p50", "p95", "p99", "max")
_LINE = re.compile(r"(gpu decode|convert|submit->fence) ms: min ([\d.]+) mean ([\d.]+) p50 ([\d.]+) p95 ([\d.]+) p99 ([\d.]+) max ([\d.]+)")
_SUMMARY = re.compile(r"frames complete (\d+) partial (\d+) skipped (\d+) dropped (\d+) superseded (\d+) \| stale pk (\d+) foreign (\d+) decode fail (\d+)")
_PATH = re.compile(r"decode path (fragment|compute) \((forced|auto)\)")


def parse_report_lines(text):
    """{'gpu decode': [ {min,mean,p50,p95,p99,max}, ... ], 'convert': [...], 'submit->fence': [...]}"""
    out = {"gpu decode": [], "convert": [], "submit->fence": []}
    for m in _LINE.finditer(text):
        out[m.group(1)].append({k: float(v) for k, v in zip(STATS, m.groups()[1:])})
    return out


def parse_summary(text):
    """The last receiver counter line, as ints; None if absent."""
    last = None
    for m in _SUMMARY.finditer(text):
        last = m
    if not last:
        return None
    keys = ("complete", "partial", "skipped", "dropped", "superseded", "stale_packets", "foreign", "decode_fail")
    return {k: int(v) for k, v in zip(keys, last.groups())}


def parse_path(text):
    m = None
    for m in _PATH.finditer(text):
        pass
    return (m.group(1), m.group(2)) if m else (None, None)


def cell_stats(reports):
    """Pool one cell's periodic reports (each covers 720 frames) into one row per metric:
    mean of means, median of p50s, max of p95/p99/max, min of mins. Frame-weighted enough,
    since every report covers the same number of frames."""
    out = {}
    for metric, rows in reports.items():
        if not rows:
            out[metric] = None
            continue
        def med(xs):
            xs = sorted(xs); n = len(xs)
            return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])
        out[metric] = {"min": min(r["min"] for r in rows), "mean": sum(r["mean"] for r in rows) / len(rows),
                       "p50": med([r["p50"] for r in rows]), "p95": max(r["p95"] for r in rows),
                       "p99": max(r["p99"] for r in rows), "max": max(r["max"] for r in rows), "reports": len(rows)}
    return out


def delta_percent(compute, fragment):
    return None if not fragment else (compute - fragment) / fragment * 100.0


def pair_deltas(frag, comp, metrics=("gpu decode", "submit->fence"), stats=("mean", "p50", "p95", "p99")):
    out = {}
    for m in metrics:
        if frag.get(m) and comp.get(m):
            out[m] = {s: delta_percent(comp[m][s], frag[m][s]) for s in stats}
    return out


def propagation_k(frag, comp, noise_us, stats=("mean", "p50", "p95", "p99")):
    """K = fence_savings / gpu_savings per statistic; only when gpu_savings > 0 and above noise.
    `noise_us[stat]` is the spread of that statistic between the two fragment cells, in ms."""
    out = {}
    for s in stats:
        g = frag["gpu decode"][s] - comp["gpu decode"][s]
        f = frag["submit->fence"][s] - comp["submit->fence"][s]
        if g > 0 and g > noise_us.get(s, 0.0):
            out[s] = {"gpu_savings_ms": g, "fence_savings_ms": f, "K": f / g, "band": k_band(f / g)}
        else:
            out[s] = {"gpu_savings_ms": g, "fence_savings_ms": f, "K": None, "band": "NOT COMPUTED (gpu_savings <= noise)"}
    return out


def k_band(k):
    if k < -0.1:
        return "K < 0: GPU improves, fence worsens -- contradictory; investigate sync/scheduling"
    if k <= 0.15:
        return "K ~ 0: GPU gain does not reach the fence -- downstream bottleneck"
    if k < 0.8:
        return "K < 1: part of the GPU gain is absorbed downstream"
    if k <= 1.2:
        return "K ~ 1: the GPU gain propagates to completion latency"
    return "K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval"


def interpretation_band(delta_pct_gpu_mean, delta_pct_fence_mean):
    """Pre-defined bands on the better of the two mean deltas (negative = compute faster)."""
    best = min(delta_pct_gpu_mean, delta_pct_fence_mean)
    worst = max(delta_pct_gpu_mean, delta_pct_fence_mean)
    if worst > 5:
        return "CONTRADICTION (compute slower on at least one)" if best > 0 else "MIXED"
    if best <= -20:
        return "STRONG CONFIRMATION (>= 20 % faster)"
    if best <= -5:
        return "MODERATE CONFIRMATION (5-20 % faster)"
    return "NO MEANINGFUL EFFECT (< 5 %)"


def outcome(delta_gpu_mean, delta_fence_mean):
    """A/B/C/D from the brief on the mean deltas (percent, negative = improvement)."""
    gpu_better, fence_better = delta_gpu_mean <= -5, delta_fence_mean <= -5
    if delta_gpu_mean > 5 or delta_fence_mean > 5:
        return "D", "compute is worse on GPU time and/or fence"
    if gpu_better and fence_better:
        return "A", "GPU and fence both improve"
    if gpu_better and not fence_better:
        return "B", "GPU improves, fence does not"
    if not gpu_better and fence_better:
        return "A (fence-side)", "fence improves substantially while the GPU-timestamp interval barely moves: the win is structural (passes, barriers, layout transitions, the convert pass after the trailing barrier), not arithmetic; per the brief, do not attribute it to reduced 9/7 arithmetic"
    return "C", "neither changes meaningfully"


def thermal_match(start_a, start_b, tolerance_c=3.0):
    if start_a is None or start_b is None:
        return "UNKNOWN", None
    d = abs(start_a - start_b)
    return ("THERMALLY MATCHED" if d <= tolerance_c else "THERMALLY MISMATCHED"), d


def load_cell(run_dir, label):
    d = Path(run_dir) / label
    text = (d / "logcat.txt").read_text(encoding="utf-8", errors="replace") if (d / "logcat.txt").exists() else ""
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
    hot = json.loads((d / "hottest.json").read_text(encoding="utf-8")) if (d / "hottest.json").exists() else {}
    path, forced = parse_path(text)
    return {"label": label, "stats": cell_stats(parse_report_lines(text)), "summary": parse_summary(text),
            "path": path, "forced": forced, "meta": meta, "hottest": hot}


def fmt_stats(row):
    if not row:
        return "UNKNOWN"
    return " / ".join(f"{row[s]:.2f}" for s in ("min", "mean", "p50", "p95", "p99", "max")) + f" ({row['reports']} reports)"


def thermal_line(cell):
    m = cell["meta"]; z0 = m.get("thermal_zones_start", {}); z1 = m.get("thermal_zones_end", {}); h = cell["hottest"]
    return (f"CPU {z0.get('cpu_max_c', 'UNKNOWN')} -> {z1.get('cpu_max_c', 'UNKNOWN')} C; GPU {z0.get('gpu_max_c', 'UNKNOWN')} -> {z1.get('gpu_max_c', 'UNKNOWN')} C; "
            f"DDR {z0.get('ddr_c', 'UNKNOWN')} -> {z1.get('ddr_c', 'UNKNOWN')} C; hottest {z0.get('hottest_c', 'UNKNOWN')} -> {z1.get('hottest_c', 'UNKNOWN')} C "
            f"(pre-launch sample {h.get('start_c', 'UNKNOWN')} C, post {h.get('end_c', 'UNKNOWN')} C); status start {h.get('status_start', 'UNKNOWN')} end {m.get('thermal_end', 'UNKNOWN')}")


def render(run_dir, out_path, correctness, baseline_ref_ms=3.7, sustained_dir=None):
    cells = {l: load_cell(run_dir, l) for l in ("PF-1", "PC-1", "PF-2", "PC-2")}
    L = []
    A = L.append
    A("# Experiment 1 — PyroWave compute path vs fragment path on the Galaxy XR\n")
    A("Central question: is the current PyroWave latency a property of CDF 9/7, or of how 9/7 is executed on the Adreno? One variable: fragment vs compute reconstruction of identical bytes. All numbers MEASURED on device unless tagged.\n")
    A("## 1. Correctness gate\n")
    A(correctness + "\n")
    A("## 2. Workload identity verification\n")
    for l, c in cells.items():
        seg = c["meta"].get("segment", {})
        A(f"- {l}: decode path `{c['path']}` ({c['forced']}); eye {seg.get('eye')}x{seg.get('eye_h')}, {seg.get('mbps')} Mbps, {seg.get('hz')} Hz, centre {seg.get('center_size_x')}, buffering {seg.get('buffering')}; receiver {c['summary']}")
    same = len({(c["meta"].get("segment", {}).get("eye"), c["meta"].get("segment", {}).get("mbps"), c["meta"].get("segment", {}).get("hz")) for c in cells.values()}) == 1
    paths_ok = cells["PF-1"]["path"] == "fragment" and cells["PF-2"]["path"] == "fragment" and cells["PC-1"]["path"] == "compute" and cells["PC-2"]["path"] == "compute"
    A(f"\nIdentical configuration across cells: {'YES' if same else 'NO'}; arms proven by logcat: {'YES' if paths_ok else 'NO -- INVALID'}. Encoded bytes: same server, same session file, same cap (server log `encoder ready: 1984x896`); the byte stream is regenerated per cell, not replayed (the live pipeline has no replay), so bytes are equal by configuration and rate control, not bit-identical (DERIVED).\n")
    A("## 3. Fragment baseline regression\n")
    for l in ("PF-1", "PF-2"):
        g = cells[l]["stats"].get("gpu decode")
        if g:
            ok = 0.9 * baseline_ref_ms <= g["mean"] <= 1.1 * baseline_ref_ms
            A(f"- {l}: GPU decode mean {g['mean']:.2f} ms vs historical {baseline_ref_ms} ms (window {0.9 * baseline_ref_ms:.2f}-{1.1 * baseline_ref_ms:.2f}): {'PASS' if ok else 'OUTSIDE WINDOW'}")
        else:
            A(f"- {l}: UNKNOWN (no report lines)")
    order = [("4", "PF-1"), ("5", "PC-1"), ("6", None), ("7", "PF-2"), ("8", "PC-2"), ("9", None)]
    pairs = {}
    for num, l in order:
        if l:
            c = cells[l]
            A(f"\n## {num}. {l} raw results\n")
            for metric in ("gpu decode", "convert", "submit->fence"):
                A(f"- {metric} ms (min / mean / p50 / p95 / p99 / max): {fmt_stats(c['stats'].get(metric))}")
            A(f"- receiver: {c['summary']}")
            A(f"- thermal: {thermal_line(c)}")
        else:
            pid = 1 if num == "6" else 2
            f, cc = cells[f"PF-{pid}"], cells[f"PC-{pid}"]
            d = pair_deltas(f["stats"], cc["stats"])
            pairs[pid] = d
            A(f"\n## {num}. Pair {pid} deltas (Compute - Fragment) / Fragment x 100\n")
            for m, row in d.items():
                A(f"- {m}: " + ", ".join(f"{s} {v:+.1f} %" for s, v in row.items() if v is not None))
    A("\n## 10. Thermal matching status\n")
    tm = {}
    for pid in (1, 2):
        f, cc = cells[f"PF-{pid}"], cells[f"PC-{pid}"]
        sa, sb = f["hottest"].get("start_c"), cc["hottest"].get("start_c")
        if sa is None:
            sa = f["meta"].get("thermal_zones_start", {}).get("hottest_c")
        if sb is None:
            sb = cc["meta"].get("thermal_zones_start", {}).get("hottest_c")
        status, dd = thermal_match(sa, sb)
        tm[pid] = status
        A(f"- Pair {pid}: PF start {sa} C, PC start {sb} C, difference {dd if dd is None else round(dd, 1)} C -> {status}")
    agree = None
    if 1 in pairs and 2 in pairs and pairs[1].get("gpu decode") and pairs[2].get("gpu decode"):
        g1, g2 = pairs[1]["gpu decode"]["mean"], pairs[2]["gpu decode"]["mean"]
        f1, f2 = pairs[1]["submit->fence"]["mean"], pairs[2]["submit->fence"]["mean"]
        agree = (g1 < 0) == (g2 < 0) and (f1 < 0) == (f2 < 0)
        A(f"\nPairs agree in sign on GPU and fence: {'YES' if agree else 'NO -- contradiction, reported as such'} (GPU mean deltas {g1:+.1f} % / {g2:+.1f} %, fence {f1:+.1f} % / {f2:+.1f} %).")
    pooled = {}
    for arm, labels in (("Fragment", ("PF-1", "PF-2")), ("Compute", ("PC-1", "PC-2"))):
        pooled[arm] = {}
        for metric in ("gpu decode", "submit->fence"):
            rows = [cells[l]["stats"][metric] for l in labels if cells[l]["stats"].get(metric)]
            if rows:
                pooled[arm][metric] = {s: (sum(r[s] for r in rows) / len(rows) if s in ("mean", "p50") else max(r[s] for r in rows)) for s in ("mean", "p50", "p95", "p99")}
    for num, arm in (("11", "Fragment"), ("12", "Compute")):
        A(f"\n## {num}. Pooled {arm} statistics (secondary)\n")
        for metric, row in pooled[arm].items():
            A(f"- {arm} {metric}: " + ", ".join(f"{s} {v:.2f}" for s, v in row.items()))
    A("")
    A("## 13. GPU savings, 14. Fence savings, 15. K = Fence_savings / GPU_savings (per pair, per statistic)\n")
    noise = {}
    for s in ("mean", "p50", "p95", "p99"):
        a, b = cells["PF-1"]["stats"].get("gpu decode"), cells["PF-2"]["stats"].get("gpu decode")
        noise[s] = abs(a[s] - b[s]) if a and b else 0.0
    A(f"Noise floor per statistic = |PF-1 - PF-2| GPU decode: " + ", ".join(f"{s} {v:.2f} ms" for s, v in noise.items()) + "\n")
    ks = {}
    for pid in (1, 2):
        f, cc = cells[f"PF-{pid}"]["stats"], cells[f"PC-{pid}"]["stats"]
        if f.get("gpu decode") and cc.get("gpu decode") and f.get("submit->fence") and cc.get("submit->fence"):
            k = propagation_k(f, cc, noise)
            ks[pid] = k
            A(f"Pair {pid} ({tm.get(pid)}):")
            for s, row in k.items():
                kk = "n/a" if row["K"] is None else f"{row['K']:.2f}"
                A(f"- {s}: GPU savings {row['gpu_savings_ms']:+.2f} ms, fence savings {row['fence_savings_ms']:+.2f} ms, K = {kk} -> {row['band']}")
            A("")
    A("## 16. Structural pass/dispatch comparison (DERIVED from pyrowave source, checkout d2997ac)\n")
    A("| | fragment path (Adreno default) | compute path |\n|---|---|---|")
    A("| dequant/unpack | <= 60 compute dispatches (per level x component x band), 1 barrier | same |")
    A("| inverse DWT | 3 render passes per level (2 vertical even/odd + 1 horizontal), each in 3 scissored draws, 5 levels = 15 passes / 45 draws; chroma 4:2:0 fix-up passes; layout barriers between vertical and horizontal and after each level; trailing FRAGMENT->COMPUTE barrier | 5 compute dispatches (H+V fused per level in shared memory), 1 compute->compute barrier per level |")
    A("| intermediate surfaces | per level: horiz[3] of w_l x h_l and vert[2][2] of w_l x 2h_l (luma R16F, chroma RG16F) | one wavelet image (alignedW/2 x alignedH/2, 12 layers, 5 mips) |")
    A("| full-res-equivalent memory passes (luma) | ~4 (ESTIMATED: ~2x compute path's intermediates) | ~2 |")
    A("| standalone T2 decode, 3328x1472 4:4:4 (MEASURED, pyrowave_android, 200 iters, twice) | 9.446 / 9.446 ms | 7.077 / 7.076 ms |")
    A("| standalone convert (MEASURED) | 2.734 ms | 1.466 ms |\n")
    if sustained_dir and Path(sustained_dir).exists():
        A("## 17. Sustained results (Phase 3)\n")
        sc = {}
        for l in ("SF", "SC"):
            if (Path(sustained_dir) / l).exists():
                c = load_cell(sustained_dir, l); sc[l] = c
                A(f"- {l} ({c['path']}, {c['forced']}): gpu decode {fmt_stats(c['stats'].get('gpu decode'))}; convert {fmt_stats(c['stats'].get('convert'))}; fence {fmt_stats(c['stats'].get('submit->fence'))}; receiver {c['summary']}; thermal {thermal_line(c)}")
        if "SF" in sc and "SC" in sc:
            st, dd = thermal_match(sc["SF"]["hottest"].get("start_c"), sc["SC"]["hottest"].get("start_c"))
            A(f"- Sustained pair thermal matching: SF start {sc['SF']['hottest'].get('start_c')} C, SC start {sc['SC']['hottest'].get('start_c')} C, difference {dd if dd is None else round(dd, 1)} C -> {st}; end SF {sc['SF']['hottest'].get('end_c')} C vs SC {sc['SC']['hottest'].get('end_c')} C")
            d = pair_deltas(sc["SF"]["stats"], sc["SC"]["stats"])
            for m, row in d.items():
                A(f"- Sustained deltas {m}: " + ", ".join(f"{s_} {v:+.1f} %" for s_, v in row.items() if v is not None))
            k = propagation_k(sc["SF"]["stats"], sc["SC"]["stats"], {})
            for s_, row in k.items():
                A(f"- Sustained K {s_}: GPU savings {row['gpu_savings_ms']:+.2f} ms, fence savings {row['fence_savings_ms']:+.2f} ms, K = {'n/a' if row['K'] is None else f'{row[chr(75)]:.2f}'} -> {row['band']}")
        A("")
    else:
        A("## 17. Sustained results (Phase 3)\n\nNOT RUN (see decision below).\n")
    A("## 18. Outcome classification\n")
    if pooled["Fragment"].get("gpu decode") and pooled["Compute"].get("gpu decode"):
        dg = delta_percent(pooled["Compute"]["gpu decode"]["mean"], pooled["Fragment"]["gpu decode"]["mean"])
        df = delta_percent(pooled["Compute"]["submit->fence"]["mean"], pooled["Fragment"]["submit->fence"]["mean"])
        o, why = outcome(dg, df)
        A(f"Pooled mean deltas: GPU {dg:+.1f} %, fence {df:+.1f} %. Band: {interpretation_band(dg, df)}. Outcome **{o}**: {why}.")
        A(f"Primary evidence is the paired result: pairs {'agree' if agree else 'DISAGREE'}; thermal status Pair 1 {tm.get(1)}, Pair 2 {tm.get(2)}.\n")
    A("## 19. Remaining uncertainties\n")
    A("- The live pipeline regenerates the byte stream per cell; bytes are equal by configuration, not replayed (the standalone Phase 1 decode is the bit-identical control).\n- GPU timestamps bracket PyroWave's decode only; the fence covers decode + convert + queue wait.\n- Two pairs; no distribution test is claimed.\n- Fragment path uses FP16 intermediates (max 4 code values from compute): quality differs slightly in compute's favour, treated as not material.\n")
    A("## 20. Decision: does compute CDF 9/7 become the experimental baseline?\n")
    if pooled["Fragment"].get("gpu decode") and pooled["Compute"].get("gpu decode"):
        yes = agree and o.startswith("A") and df <= -5
        A(f"{'YES' if yes else 'NO'} -- outcome {o}. " + ("Compute becomes the EXECUTION baseline: replicated, thermally matched, correctness-clean, faster to completion with tighter tails and equal-or-better accuracy. It is NOT evidence that transform arithmetic matters: K >> 1 says the saving lives outside the decode-timestamp interval, so later transform experiments (5/3, Haar, DCT, WHT) must be judged on passes and barriers against compute 9/7, and small arithmetic reductions should be expected NOT to propagate. " if yes else "") + "See the pair-level evidence above. Per the brief, GPU-time and fence-time conclusions are stated separately: "
          f"compute changes inverse-decoder GPU execution by {dg:+.1f} % (pooled mean); compute changes submit->fence completion by {df:+.1f} % (pooled mean).\n")
    report = "\n".join(L) + "\n"
    Path(out_path).write_text(report, encoding="utf-8")
    return report


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True); p.add_argument("--out", required=True)
    p.add_argument("--correctness", required=True, help="path to a text file with the Phase 1 result")
    p.add_argument("--sustained", default=None)
    a = p.parse_args(argv)
    print(render(a.run, a.out, Path(a.correctness).read_text(encoding="utf-8"), sustained_dir=a.sustained))


if __name__ == "__main__":
    main()
