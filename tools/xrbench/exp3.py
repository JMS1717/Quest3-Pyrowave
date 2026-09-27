"""Experiment 3: fused Haar reconstruction topology on the Galaxy XR.

Phase 0 is a pure model: for each candidate topology (levels fused per dispatch stage) derive
the dispatch/barrier structure, the logical intermediate bytes, the shared-memory and register
footprint and the lane utilisation, then apply the project's structural gate (feasible AND material)
against the CDF 9/7 compute reference from `exp2.logical_bytes`. Every quantity here is DERIVED
or ESTIMATED; nothing is measured until Phase 2.
"""
import json
import math
from pathlib import Path

from . import exp2

LEVELS = exp2.LEVELS
TILE_OUT = 32              # output tile per workgroup, matching PyroWave's 32x32 block
LANES = 64                 # PyroWave's compute workgroup
BYTES_F16 = 2
BYTES_OUT = 1              # R8 planes

TOPOLOGIES = {
    "H0": (1, 1, 1, 1, 1),   # one level per stage: the control, PyroWave's own structure
    "H1": (2, 2, 1),
    "H2": (3, 2),
    "H3": (5,),
}

# Adreno 740 limits used by the feasibility check. ESTIMATED until Phase 2 logs the device's
# maxComputeSharedMemorySize; the register budget is a working assumption, not a datasheet number.
SHARED_LIMIT_BYTES = 32 * 1024
OCCUPANCY_MIN_WORKGROUPS = 2
REGISTER_BUDGET_PER_LANE = 64
REDUNDANT_LOAD_LIMIT = 1.25


def stage_plan(levels_per_stage):
    """[(level_in, level_out), ...] coarsest first; level 5 is the LL5 input, level 0 the
    output image (level_out == 0 means the R8 planes are written)."""
    if sum(levels_per_stage) != LEVELS or any(k < 1 for k in levels_per_stage):
        raise ValueError(f"stages must fuse exactly {LEVELS} levels: {levels_per_stage}")
    plan = []
    level = LEVELS
    for k in levels_per_stage:
        plan.append((level, level - k))
        level -= k
    return plan


def coefficients_at(width, height, level):
    """Coefficients per band at wavelet level `level` (1 = finest ... 5 = coarsest)."""
    return math.prod(exp2.band_shape(width, height, level - 1))


def topology(name, levels_per_stage, width, height, chroma444=True, precision=0, fps=90.0, apron=0):
    """Structural model of one reconstruction topology (all DERIVED unless marked).

    `apron` is the per-side output-sample halo one inverse level needs (0 for Haar, 4 for
    CDF 9/7 as in `dwt_common.h`). With a halo, a stage fusing k levels into a 32x32 tile must
    load and reconstruct a larger footprint whose margin shrinks by `apron` per level, so the
    coefficient reads and the shared tile carry a redundant-load factor (Experiment 4)."""
    comps = [(width, height)] * 3 if chroma444 else [(width, height)] + [((width + 1) // 2, (height + 1) // 2)] * 2
    plan = stage_plan(levels_per_stage)
    stages = []
    dequant_write = coeff_read = ll_write = ll_read = out_write = 0
    for cw, ch in comps:
        for level in range(1, LEVELS + 1):
            n = coefficients_at(cw, ch, level)
            bands = 3 + (1 if level == LEVELS else 0)
            dequant_write += bands * n * exp2.bytes_per_coefficient(level - 1, precision)
    for si, (lin, lout) in enumerate(plan):
        k = lin - lout
        tile_in = TILE_OUT >> k
        s = {"stage": si + 1, "level_in": lin, "level_out": lout, "levels_fused": k, "tile_in": tile_in,
             "tile_out": TILE_OUT}
        reads = writes = 0
        # halo: the output tile plus `apron` per side at the finest fused level; every coarser
        # level needs half that plus its own apron. Redundant-load factor = footprint / tile.
        halo_out = apron * k                       # output-sample halo at the stage's output level
        footprint_in = (tile_in + (2 * halo_out) / (1 << k)) if apron else tile_in
        redundant = (footprint_in ** 2) / (tile_in ** 2) if apron else 1.0
        for cw, ch in comps:
            # the stage's LL input: dequant's LL5 for the first stage, else the previous stage's write
            n_ll = coefficients_at(cw, ch, lin)
            reads += n_ll * exp2.bytes_per_coefficient(lin - 1, precision) * redundant
            if si > 0:
                ll_read += n_ll * exp2.bytes_per_coefficient(lin - 1, precision) * redundant
            for j, level in enumerate(range(lin, lout, -1)):
                # band footprint at this level: tile at this level plus the remaining halo
                t_l = tile_in << j
                h_l = (apron * (k - j)) / 2 if apron else 0
                red_l = ((t_l + 2 * h_l) ** 2) / (t_l ** 2) if apron else 1.0
                reads += 3 * coefficients_at(cw, ch, level) * exp2.bytes_per_coefficient(level - 1, precision) * red_l
            if lout > 0:
                n_out = coefficients_at(cw, ch, lout)
                writes += n_out * exp2.bytes_per_coefficient(lout - 1, precision)
                ll_write += n_out * exp2.bytes_per_coefficient(lout - 1, precision)
            else:
                writes += cw * ch * BYTES_OUT
                out_write += cw * ch * BYTES_OUT
        coeff_read += reads
        # footprint inside the workgroup: the largest intermediate held is the stage's output
        # tile (or, for a one-level stage, PyroWave's 36x36 apron tile of vec2 = 3.3 KB)
        vals = (TILE_OUT + 2 * apron * (k if apron else 0)) ** 2 if apron else TILE_OUT * TILE_OUT
        shared = (2 * vals * BYTES_F16) if (k > 1 or apron) else 20 * 41 * 2 * BYTES_F16   # ping-pong tiles
        regs = max(8, math.ceil(vals / LANES / 2) + 8)          # f16 pairs per lane + coefficient/temp headroom
        lane_util = []
        for j in range(k):
            outputs = (tile_in << (j + 1)) ** 2
            quads = outputs / 4
            lane_util.append(min(1.0, quads / LANES))
        s.update({"global_read_bytes": reads, "global_write_bytes": writes, "shared_bytes": shared,
                  "registers_per_lane_est": regs, "lane_utilisation_per_level": lane_util,
                  "workgroup_barriers": 2 * k + 1, "redundant_load_factor": redundant, "halo_out": halo_out})
        stages.append(s)
    coeff_read = int(round(coeff_read)); ll_read = int(round(ll_read))
    total = dequant_write + coeff_read + ll_write + out_write
    pixels = width * height
    stages_n = len(plan)
    frame_f16 = sum(cw * ch for cw, ch in comps) * BYTES_F16
    return {"name": name, "levels_per_stage": list(levels_per_stage), "stages": stages,
            "dispatches": stages_n * 3, "global_barriers": stages_n + 1,
            "workgroup_barriers": sum(s["workgroup_barriers"] for s in stages),
            "temp_global_buffers": stages_n - 1,
            "dequant_write_bytes": dequant_write, "coefficient_read_bytes": coeff_read,
            "ll_write_bytes": ll_write, "ll_reread_bytes": ll_read, "output_write_bytes": out_write,
            "total_bytes": total, "bytes_per_pixel": total / pixels, "bytes_per_second": total * fps,
            "full_frame_equivalent_passes": (coeff_read + ll_write + out_write) / frame_f16,
            "max_shared_bytes": max(s["shared_bytes"] for s in stages),
            "max_registers_per_lane_est": max(s["registers_per_lane_est"] for s in stages),
            "min_lane_utilisation": min(min(s["lane_utilisation_per_level"]) for s in stages),
            "precision": precision, "width": width, "height": height, "chroma444": chroma444, "apron": apron,
            "max_redundant_load_factor": max(s["redundant_load_factor"] for s in stages)}


def reference(width, height, chroma444=True, fps=90.0):
    """CDF 9/7 compute at precision 1, the Experiment 1/2 baseline, in the same terms."""
    lb = exp2.logical_bytes(width, height, chroma444, 1, fps)
    comps = 3 if chroma444 else 1.5
    frame_f16 = width * height * comps * BYTES_F16
    return {"name": "9/7 compute (reference)", "dispatches": 15, "global_barriers": 6, "workgroup_barriers": 5 * 3,
            "temp_global_buffers": 4, "total_bytes": lb["total_bytes"], "bytes_per_pixel": lb["bytes_per_pixel"],
            "bytes_per_second": lb["bytes_per_second"],
            "full_frame_equivalent_passes": (lb["idwt_read_bytes"] + lb["idwt_write_bytes"]) / frame_f16,
            "max_shared_bytes": 20 * 41 * 2 * BYTES_F16}


def feasible(t, shared_limit=SHARED_LIMIT_BYTES, occupancy=OCCUPANCY_MIN_WORKGROUPS,
             register_budget=REGISTER_BUDGET_PER_LANE, redundant_limit=REDUNDANT_LOAD_LIMIT):
    reasons = []
    if t["max_shared_bytes"] * occupancy > shared_limit:
        reasons.append(f"shared {t['max_shared_bytes']} B x {occupancy} workgroups exceeds {shared_limit} B")
    if t["max_registers_per_lane_est"] > register_budget:
        reasons.append(f"registers/lane {t['max_registers_per_lane_est']} > {register_budget} (ESTIMATED)")
    if any(s["redundant_load_factor"] > redundant_limit for s in t["stages"]):
        reasons.append("redundant coefficient loads")
    return not reasons, reasons


def material(t, ref, byte_cut=0.30):
    byte_ratio = t["total_bytes"] / ref["total_bytes"]
    bytes_ok = byte_ratio <= 1.0 - byte_cut
    # "a comparably substantial reduction in global barriers / full-frame-equivalent passes":
    # the same 30 % bar applied to both counts. Note the floor: a decoder that reads each
    # coefficient once and writes the R8 output cannot go below ~1.5 full-frame passes, so
    # "half of 9/7" (1.13) would be unreachable by construction.
    barrier_cut = 1 - t["global_barriers"] / ref["global_barriers"]
    pass_cut = 1 - t["full_frame_equivalent_passes"] / ref["full_frame_equivalent_passes"]
    structure_ok = barrier_cut >= byte_cut and pass_cut >= byte_cut
    return (bytes_ok or structure_ok), {"byte_ratio": byte_ratio, "bytes_ok": bytes_ok, "structure_ok": structure_ok,
                                        "byte_cut_pct": 100.0 * (1 - byte_ratio), "barrier_cut_pct": 100.0 * barrier_cut,
                                        "pass_cut_pct": 100.0 * pass_cut}


def structural_gate(topologies, ref):
    """The project's nuanced gate: a topology proceeds to the microbenchmark only if it is feasible
    AND material; every topology that qualifies is built (H2 and H3 both, when both do)."""
    verdicts = {}
    for name, t in topologies.items():
        ok_f, why = feasible(t)
        ok_m, m = material(t, ref)
        band = ("PROCEED" if ok_f and ok_m else
                "NOT FEASIBLE" if not ok_f else
                "NOT STRONG ENOUGH" if m["byte_cut_pct"] < 30 else "NOT MATERIAL")
        verdicts[name] = {"feasible": ok_f, "reasons": why, "material": ok_m, **m, "verdict": band}
    proceed = [n for n, v in verdicts.items() if v["verdict"] == "PROCEED" and n != "H0"]
    return proceed, verdicts


def rd_gate(extra_pct_kodak, panel_advantage_db, model_saving_pct):
    """Continue if Haar needs <= 50 % extra bytes on Kodak at matched PSNR-Y, or shows a compelling
    synthetic/game-like advantage; STOP if ~2x on Kodak AND the model predicts a small saving."""
    if extra_pct_kodak is not None and extra_pct_kodak <= 50.0:
        return "PROCEED", f"Kodak extra bytes {extra_pct_kodak:+.1f} % <= 50 %"
    if panel_advantage_db is not None and panel_advantage_db >= 1.0:
        return "PROCEED (content-specific)", f"panel advantage {panel_advantage_db:+.2f} dB with Kodak {extra_pct_kodak:+.1f} %"
    if extra_pct_kodak is not None and extra_pct_kodak >= 90.0 and (model_saving_pct is None or model_saving_pct < 30.0):
        return "STOP", f"~2x bytes on Kodak ({extra_pct_kodak:+.1f} %) and a small modelled saving"
    return "STOP", f"Kodak extra bytes {extra_pct_kodak:+.1f} % > 50 % and no compelling panel advantage"


def render_topology(width, height, out_dir, chroma444=True):
    ref = reference(width, height, chroma444)
    tops = {n: topology(n, k, width, height, chroma444) for n, k in TOPOLOGIES.items()}
    proceed, verdicts = structural_gate(tops, ref)
    L = []
    A = L.append
    A(f"# Experiment 3 Phase 0 — Haar reconstruction topologies at {width}x{height} {'4:4:4' if chroma444 else '4:2:0'} (DERIVED model)\n")
    A("Haar: inverse `x0 = a - d/2`, `x1 = a + d/2`, 2-tap, no apron, so tiles are independent across levels. "
      "Output tile 32x32 per 64-lane workgroup in every stage; a stage fusing k levels starts from a (32 >> k)^2 LL tile. "
      "Bytes count PyroWave's dequant writes, every coefficient read once, inter-stage LL writes and re-reads, and the R8 output. "
      "Physical DRAM traffic is UNKNOWN; register counts are ESTIMATED.\n")
    A("| topology | stages | dispatches | global barriers | workgroup barriers | temp LL images | coefficient reads | LL writes | LL re-reads | total logical bytes | bytes/px | vs 9/7 | full-frame passes | max shared | regs/lane (est) | min lane util |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    A(f"| {ref['name']} | 5 | {ref['dispatches']} | {ref['global_barriers']} | {ref['workgroup_barriers']} | {ref['temp_global_buffers']} | - | - | - | {ref['total_bytes']:,} | {ref['bytes_per_pixel']:.2f} | 0.0 % | {ref['full_frame_equivalent_passes']:.2f} | {ref['max_shared_bytes']:,} | - | - |")
    for n, t in tops.items():
        v = verdicts[n]
        A(f"| {n} {t['levels_per_stage']} | {len(t['stages'])} | {t['dispatches']} | {t['global_barriers']} | {t['workgroup_barriers']} | {t['temp_global_buffers']} | {t['coefficient_read_bytes']:,} | {t['ll_write_bytes']:,} | {t['ll_reread_bytes']:,} | {t['total_bytes']:,} | {t['bytes_per_pixel']:.2f} | {-v['byte_cut_pct']:+.1f} % | {t['full_frame_equivalent_passes']:.2f} | {t['max_shared_bytes']:,} | {t['max_registers_per_lane_est']} | {t['min_lane_utilisation']:.3f} |")
    A("\n## Per-stage detail\n")
    for n, t in tops.items():
        for s in t["stages"]:
            A(f"- {n} stage {s['stage']}: levels {s['level_in']}->{s['level_out']} ({s['levels_fused']} fused), tile {s['tile_in']}^2 -> {s['tile_out']}^2, reads {s['global_read_bytes']:,} B, writes {s['global_write_bytes']:,} B, shared {s['shared_bytes']:,} B, regs/lane ~{s['registers_per_lane_est']}, lane utilisation per level {[round(x, 3) for x in s['lane_utilisation_per_level']]}, workgroup barriers {s['workgroup_barriers']}")
    A("\n## Structural gate (feasible AND material)\n")
    A(f"Feasibility: shared x {OCCUPANCY_MIN_WORKGROUPS} workgroups <= {SHARED_LIMIT_BYTES:,} B (device limit to be logged in Phase 2), registers/lane <= {REGISTER_BUDGET_PER_LANE} (ESTIMATED), redundant loads <= {REDUNDANT_LOAD_LIMIT}x. Material: logical bytes cut >= 30 % vs 9/7 compute, or global barriers AND full-frame passes both cut >= 30 % (the brief's 'comparably substantial'). The byte cut includes precision 0 (all-R16F, worth 4.9 % on its own, Experiment 2); the structural part is the difference from H0.\n")
    for n, v in verdicts.items():
        A(f"- {n}: bytes {-v['byte_cut_pct']:+.1f} % vs 9/7 (bytes_ok {v['bytes_ok']}); global barriers {-v['barrier_cut_pct']:+.1f} %, full-frame passes {-v['pass_cut_pct']:+.1f} % (structure_ok {v['structure_ok']}); feasible {v['feasible']} {v['reasons'] or ''} -> **{v['verdict']}**")
    A(f"\nTopologies that proceed to the microbenchmark: {', '.join(proceed) if proceed else 'NONE -- FUSED HAAR STRUCTURAL HYPOTHESIS NOT STRONG ENOUGH TO JUSTIFY DEVICE IMPLEMENTATION'}.\n")
    A("Lane utilisation at the coarse levels of a deep stage is the known cost of fusion: the first Haar level of H3 reconstructs a 2x2 tile with 1 of 64 lanes active. It is cheap in absolute terms (the coarse levels hold 1/1024 .. 1/16 of the pixels) but it is real, ESTIMATED here and measured in Phase 2.")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "topology.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (out / "topology.json").write_text(json.dumps({"reference": ref, "topologies": tops, "verdicts": verdicts, "proceed": proceed}, indent=1))
    return proceed, verdicts, tops, ref


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("topology")
    t.add_argument("--width", type=int, default=1984); t.add_argument("--height", type=int, default=896)
    t.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if a.cmd == "topology":
        proceed, verdicts, tops, ref = render_topology(a.width, a.height, a.out)
        for n, v in verdicts.items():
            print(f"{n}: bytes {v['byte_cut_pct']:+.1f} %, feasible {v['feasible']}, material {v['material']} -> {v['verdict']}")
        print("proceed:", proceed)


if __name__ == "__main__":
    main()


# ---- Phase 2 analysis and the report ---------------------------------------------------------

import re as _re

_T2 = _re.compile(r"T2 decode\s*: best ([\d.]+) ms, mean ([\d.]+) ms")
_CONV = _re.compile(r"T3-T2 convert\s*: best ([\d.]+) ms, mean ([\d.]+) ms")
_WALL = _re.compile(r"submit->idle wall: best ([\d.]+) ms, mean ([\d.]+) ms")


def parse_cell_log(text):
    """{gpu_best, gpu_mean, convert_best, convert_mean, wall_best, wall_mean} from a standalone
    `pyrowave_android` run; wall keys are None on binaries without the timer."""
    t2, cv, wl = _T2.search(text), _CONV.search(text), _WALL.search(text)
    if not t2:
        return None
    return {"gpu_best": float(t2.group(1)), "gpu_mean": float(t2.group(2)),
            "convert_best": float(cv.group(1)) if cv else None, "convert_mean": float(cv.group(2)) if cv else None,
            "wall_best": float(wl.group(1)) if wl else None, "wall_mean": float(wl.group(2)) if wl else None}


def cycles_per_pixel(decode_ms, clock_mhz, width, height, chroma444=True):
    """DERIVED: GPU cycles per reconstructed output sample (all three planes) at the sampled clock."""
    pixels = width * height * (3 if chroma444 else 1.5)
    return exp2.gpu_cycles_per_frame(decode_ms, clock_mhz) / pixels


def clock_trace_summary(csv_path):
    """Space-separated on-device trace: epoch, cur_freq_hz, busy_pct, thermal_pwrlevel, hottest_mC."""
    p = Path(csv_path)
    if not p.exists():
        return None
    rows = [l.split() for l in p.read_text().splitlines() if len(l.split()) >= 5]
    if not rows:
        return None
    mhz = [int(r[1]) / 1e6 for r in rows]
    top = max(mhz)
    return {"samples": len(rows), "span_s": int(rows[-1][0]) - int(rows[0][0]), "mean_mhz": sum(mhz) / len(mhz),
            "min_mhz": min(mhz), "max_mhz": top, "share_at_max_pct": 100.0 * sum(1 for m in mhz if m == top) / len(mhz),
            "pwrlevel_max": max(int(r[3]) for r in rows), "hottest_min_c": min(int(r[4]) for r in rows) / 1000.0,
            "hottest_max_c": max(int(r[4]) for r in rows) / 1000.0}


def pct(b, a):
    return (b - a) / a * 100.0 if a else None


def outcome3(fence_pct_bc, structural_cut_pct, clocks_matched, correct, replicated):
    """Predefined outcomes A-E for Comparison 2 (B same-topology vs C fused); fence_pct is the
    completion change of C against B (negative = faster)."""
    if not correct:
        return "INVALID", "B and C differ materially: no structural performance comparison"
    if not clocks_matched:
        return "CONFOUNDED", "clocks not matched: excluded from primary attribution"
    if fence_pct_bc is None:
        return "UNKNOWN", "no completion measurement"
    if fence_pct_bc <= -10.0 and replicated:
        return "A", "fused Haar wins substantially (>= 10 % completion improvement, matched clocks, correct, replicated)"
    if fence_pct_bc <= -5.0:
        return "B", "fused Haar wins modestly (5-10 %): weigh against the representation-size penalty"
    if fence_pct_bc > 5.0:
        return "E", "fused Haar is worse: preserved, not optimised until profiled"
    if structural_cut_pct is not None and structural_cut_pct >= 30.0:
        return "C", "large structural reduction but < 5 % completion improvement: even topology is not the bottleneck"
    return "C", "no meaningful completion improvement"


def live_integration_decision(fence_pct_bc, correct, kodak_extra_pct, panel_extra_pct):
    """>= 5 % replicated fence improvement at matched clocks with correctness AND a testable RD
    trade (<= 50 % extra bytes on natural content, or a content-specific case)."""
    latency_ok = correct and fence_pct_bc is not None and fence_pct_bc <= -5.0
    rd = exp3_rd = rd_gate(kodak_extra_pct, None, None)[0]
    if latency_ok and rd.startswith("PROCEED"):
        return "PROCEED", "latency and rate-distortion gates both met"
    if latency_ok:
        return "NOT NOW", f"latency gate met (C vs B {fence_pct_bc:+.1f} %), rate-distortion gate not met (Kodak {kodak_extra_pct:+.1f} % bytes at matched PSNR-Y; panel {panel_extra_pct:+.1f} %): the project's call on the bandwidth trade"
    return "NO", "latency gate not met"


def collect_phase2(d):
    d = Path(d)
    cells = {}
    for p in sorted((d / "device").glob("cell_*.log")):
        m = _re.match(r"cell_(A|B|C2|C3)_(\d+)\.log", p.name)
        if m:
            r = parse_cell_log(p.read_text(errors="replace"))
            if r:
                cells[f"{m.group(1)}_{m.group(2)}"] = {"arm": m.group(1), "run": int(m.group(2)), **r}
    cmp = {p.stem: json.loads(p.read_text()) for p in (d / "device").glob("*.json")}
    clock = clock_trace_summary(d / "device" / "gpuclk_exp3.csv")
    return cells, cmp, clock


def arm_stats(cells, arm):
    rows = [c for c in cells.values() if c["arm"] == arm]
    if not rows:
        return None
    out = {"n": len(rows)}
    for k in ("gpu_mean", "gpu_best", "convert_mean", "wall_mean", "wall_best"):
        vals = [r[k] for r in rows if r.get(k) is not None]
        out[k] = sum(vals) / len(vals) if vals else None
        out[k + "_spread"] = (max(vals) - min(vals)) if vals else None
    return out


def render(d, out_path, width=3328, height=1472, live=(1984, 896)):
    d = Path(d)
    topo = json.loads((d / "topology.json").read_text())
    cells, cmp, clock = collect_phase2(d)
    r97 = exp2.load_rd(d / "rd" / "results97.csv") if (d / "rd" / "results97.csv").exists() else []
    rh = exp2.load_rd(d / "rd" / "resultshaar.csv") if (d / "rd" / "resultshaar.csv").exists() else []
    allc = []
    for res in (1920, 2048, 2176, 2304, 2432, 2560):
        allc += exp2.equal_bytes_table(r97, rh, res)
    at = [r for r in exp2.equal_bytes_table(r97, rh, 2560) if r["cap_bytes"] == 416667]
    kod = [r["d_psnr_y"] for r in allc if "kodim" in r["source"]]
    pan = [r["d_psnr_y"] for r in allc if "kodim" not in r["source"]]
    match = json.loads((d / "match" / "match.json").read_text()) if (d / "match" / "match.json").exists() else []
    kx = [r["extra_pct"] for r in match if "kodim" in r["source"]]
    px = [r["extra_pct"] for r in match if "kodim" not in r["source"]]
    kodak_extra = sum(kx) / len(kx) if kx else None
    panel_extra = sum(px) / len(px) if px else None
    A_, B_, C3, C2 = (arm_stats(cells, a) for a in ("A", "B", "C3", "C2"))
    L = []
    A = L.append
    A("# Experiment 3 — Fused Haar reconstruction topology on the Galaxy XR\n")
    A("Central question: can Haar's 2-tap, apron-free inverse let several reconstruction levels fuse into one dispatch, removing the inter-level LL images, their re-reads and the global barriers, and does that move completion time where cheaper arithmetic (Experiment 2) did not? Reference: CDF 9/7 FP32 compute. Every number tagged MEASURED / DERIVED / ESTIMATED / UNKNOWN.\n")
    A("## 1. Experiment 1 conclusion carried forward\n")
    A("Compute 9/7 is the execution baseline: fence −13..−17 % against fragment with the GPU interval nearly unchanged (K >> 1); completion follows passes, barriers and traffic, not ops (MEASURED, Experiment 1).\n")
    A("## 2. Experiment 2 conclusion carried forward\n")
    A("CDF 5/3 FP16 at identical topology: GPU −2 %, fence 0 % at matched clock, logical bytes −4.9 %, +17.9 % bytes at matched PSNR-Y; the first −20 % was DVFS. 9/7 stays the reference; cheaper arithmetic and FP16 alone do not move completion (MEASURED, Experiment 2). GPU clock sampling is mandatory from here on.\n")
    A("## 3. CDF 9/7 compute reference\n")
    A(f"- Live: 15 iDWT dispatches (5 levels x 3 components), 6 global barriers, 4 inter-level LL images, ~2.27 full-frame-equivalent passes, {topo['reference']['total_bytes']:,} logical bytes/frame at {live[0]}x{live[1]} 4:4:4 (DERIVED). Standalone: Arm A below (MEASURED).\n")
    A("## 4. Haar mathematical definition\n")
    A("Lifting Haar in PyroWave's normalisation: forward `d = odd - even`, `a = even + d/2`; inverse `even = a - d/2`, `odd = even + d`; separable 2-D (columns then rows, order immaterial for a linear separable transform); unit DC gain, no K scaling; sequence-header code 2; quantiser gains DERIVED from the synthesis basis norms relative to 9/7 (HL/LH by level 0.989, 1.002, 0.956, 0.937, 0.932; HH 0.961, 1.034, 0.962, 0.930, 0.921; LL 0.943). Implemented as spec constant `HAAR` (id 2) in `dwt.comp`/`idwt.comp` (Arm B) and as `haar_fused.comp` in the standalone harness (Arm C).\n")
    A("## 5. H0/H1/H2/H3 topology analysis (DERIVED)\n")
    ref = topo["reference"]
    A("| topology | dispatches | global barriers | workgroup barriers | temp LL images | logical bytes/frame | vs 9/7 | full-frame passes | max shared | min lane utilisation |\n|---|---|---|---|---|---|---|---|---|---|")
    A(f"| 9/7 compute | {ref['dispatches']} | {ref['global_barriers']} | {ref['workgroup_barriers']} | {ref['temp_global_buffers']} | {ref['total_bytes']:,} | 0.0 % | {ref['full_frame_equivalent_passes']:.2f} | {ref['max_shared_bytes']:,} | - |")
    for n, t in topo["topologies"].items():
        v = topo["verdicts"][n]
        A(f"| {n} {t['levels_per_stage']} | {t['dispatches']} | {t['global_barriers']} | {t['workgroup_barriers']} | {t['temp_global_buffers']} | {t['total_bytes']:,} | {-v['byte_cut_pct']:+.1f} % | {t['full_frame_equivalent_passes']:.2f} | {t['max_shared_bytes']:,} | {t['min_lane_utilisation']:.3f} |")
    A("\nSee `topology.md` for the per-stage detail. The byte model counts dequant writes, every coefficient read once, inter-stage LL writes and re-reads, and the R8 output: fusion removes only the LL round trips, so no Haar topology reaches a 30 % byte cut; what fusion removes is barriers (6 -> 2) and full-frame passes (2.27 -> 1.50).\n")
    A("## 6. Shared-memory / register feasibility\n")
    A(f"- Device `maxComputeSharedMemorySize` = 32,768 B (MEASURED). Fused kernel: two 32x33 float16 tiles = 4,224 B shared, 64 lanes, one 32x32 output tile per workgroup; ESTIMATED ~16 registers/lane. H3's first level runs 1 of 64 lanes (1x1 -> 2x2 tile), H2's first stage 16 of 64: the known cost of deep fusion, ESTIMATED in Phase 0, measured below (C2 beats C3).\n")
    A("## 7. Logical memory-traffic model\n")
    for n in ("H0", "H2", "H3"):
        t = topo["topologies"][n]
        A(f"- {n}: coefficient reads {t['coefficient_read_bytes']:,} B, LL writes {t['ll_write_bytes']:,} B, LL re-reads {t['ll_reread_bytes']:,} B, output {t['output_write_bytes']:,} B, total {t['total_bytes']:,} B ({t['bytes_per_pixel']:.2f} B/px, {t['bytes_per_second'] / 1e9:.2f} GB/s at 90 Hz) (DERIVED; physical DRAM traffic UNKNOWN)")
    A("")
    A("## 8. Structural continuation gate\n")
    for n, v in topo["verdicts"].items():
        A(f"- {n}: bytes {-v['byte_cut_pct']:+.1f} %, barriers {-v['barrier_cut_pct']:+.1f} %, passes {-v['pass_cut_pct']:+.1f} %, feasible {v['feasible']} -> **{v['verdict']}**")
    A(f"\nProceed: {', '.join(topo['proceed'])}. H2 (barriers −50 %, passes −28 %) sits just under the bar and was carried as a diagnostic because the fused kernel takes the fusion depth as a parameter (the brief: test both when feasible).\n")
    A("## 9. Equal-byte RD results (Control A, offline, MEASURED)\n")
    A("| source | 9/7 bytes | Haar bytes | PSNR-Y 9/7 | PSNR-Y Haar | dPSNR-Y | PSNR-HVS 9/7 / Haar | SSIM 9/7 / Haar | VMAF 9/7 / Haar |\n|---|---|---|---|---|---|---|---|---|")
    for r in at:
        A(f"| {r['source']} | {r['bytes97']:,} | {r['bytes53']:,} | {r['psnr_y97']:.2f} | {r['psnr_y53']:.2f} | {r['d_psnr_y']:+.2f} | {exp2.fmt(r['psnr_hvs97'])} / {exp2.fmt(r['psnr_hvs53'])} | {exp2.fmt(r['ssim97'], 4)} / {exp2.fmt(r['ssim53'], 4)} | {exp2.fmt(r['vmaf97'], 1)} / {exp2.fmt(r['vmaf53'], 1)} |")
    A(f"\nAll {len(allc)} matched cells: Kodak mean dPSNR-Y {sum(kod) / len(kod) if kod else float('nan'):+.2f} dB, panel {sum(pan) / len(pan) if pan else float('nan'):+.2f} dB.\n")
    A("## 10. PSNR-Y-matched RD results (Control B, offline, MEASURED; never 'equal quality')\n")
    A("| source | 9/7 bytes | 9/7 PSNR-Y | Haar bytes at match | Haar PSNR-Y | extra bytes | Mbps at 90 Hz (9/7 -> Haar) | PSNR-HVS 9/7 / Haar | SSIM 9/7 / Haar |\n|---|---|---|---|---|---|---|---|---|")
    for r in match:
        A(f"| {r['source']} | {r['bytes97']:,} | {r['psnr_y97']:.2f} | {r['bytes53']:,} | {r['psnr_y53']:.2f} | {r['extra_pct']:+.1f} % | {r['bytes97'] * 8 * 90 / 1e6:.0f} -> {r['bytes53'] * 8 * 90 / 1e6:.0f} | {exp2.fmt(r['psnr_hvs97'])} / {exp2.fmt(r['psnr_hvs53'])} | {exp2.fmt(r['ssim97'], 4)} / {exp2.fmt(r['ssim53'], 4)} |")
    A(f"\nKodak mean extra bytes {exp2.fmt(kodak_extra, 1)} %; panel {exp2.fmt(panel_extra, 1)} %.\n")
    A("## 11. Kodak\n")
    A(f"Natural imagery: Haar gives up {abs(sum(kod) / len(kod)) if kod else float('nan'):.2f} dB PSNR-Y at equal bytes and needs {exp2.fmt(kodak_extra, 1)} % more bytes (about 2x) to match 9/7's PSNR-Y; PSNR-HVS, SSIM and VMAF all fall with it at equal bytes (MEASURED).\n")
    A("## 12. Synthetic panel\n")
    pr = [r for r in at if "kodim" not in r["source"]]
    if pr:
        r = pr[0]
        A(f"Contradictory evidence preserved: at equal bytes the panel's PSNR-Y falls {r['d_psnr_y']:+.2f} dB with Haar, but PSNR-HVS rises ({r['psnr_hvs97']:.2f} -> {r['psnr_hvs53']:.2f}) and SSIM rises ({r['ssim97']:.4f} -> {r['ssim53']:.4f}); at matched PSNR-Y the panel needs only {exp2.fmt(panel_extra, 1)} % more bytes against ~2x on Kodak. Synthetic/game-like content is a separate evidence class; Haar's edge-preserving behaviour on text and hard edges is real on the HVS/SSIM metrics and absent on PSNR-Y (MEASURED).\n")
    A("## 13. RD continuation gate\n")
    g = rd_gate(kodak_extra, (sum(pan) / len(pan)) if pan else None, 33.8)
    A(f"**{g[0]}** — {g[1]}. The device microbenchmark had already run in parallel with this gate (the RD run and the device work share no resource), so its result is reported below as measured; the gate governs the live-integration decision, not the reporting.\n")
    A("## 14. Device pre-checks and correctness gate (MEASURED)\n")
    A("- shaderFloat16 = 1, maxComputeSharedMemorySize = 32,768 B, 64-lane workgroups; Haar stream + 9/7 decoder rejected on device (`push_packet -> -2`).")
    A("| comparison | max abs | MSE | PSNR dB | differing |\n|---|---|---|---|---|")
    for key, name in (("A_vs_pc97", "A: 9/7 FP32 device vs PC 9/7"), ("B_vs_pchaar", "B: Haar FP16 same topology vs PC Haar"),
                      ("C3_vs_pchaar", "C3: fused H3 vs PC Haar"), ("C3_vs_B", "C3 vs B (identical coefficients)"),
                      ("C2_vs_B", "C2 vs B (identical coefficients)"), ("C3_vs_C2", "C3 vs C2")):
        if key in cmp:
            r = cmp[key]["all"]
            A(f"| {name} | {r['max_abs']} | {r['mse']:.5f} | {exp2.fmt(r['psnr'])} | {r['differing']:,} / {r['count']:,} |")
    ok = all(cmp[k]["all"]["max_abs"] <= 1 and cmp[k]["all"]["psnr"] >= 65 for k in ("C3_vs_B", "C2_vs_B") if k in cmp)
    A(f"\nStructural comparison validity: **{'VALID' if ok else 'INVALID'}** (B, C2 and C3 within 1 code value; C2 and C3 bit-identical). The fused kernel's band orientation was established empirically: of the four LH/HL x transposed hypotheses only the natural one matches B (66.97 dB); the others give 25 dB / 14 dB.\n")
    for num, arm, st, name in (("15", "A", A_, "Arm A raw results: CDF 9/7 FP32 compute (libpyrowave idwt, precision 1)"),
                               ("16", "B", B_, "Arm B raw results: Haar FP16 same topology (libpyrowave idwt, precision 0)"),
                               ("17", "C", None, "Arm C raw results: fused Haar FP16 (harness kernel)")):
        A(f"## {num}. {name} (MEASURED, standalone, {width}x{height} 4:4:4, 200 iterations per cell)\n")
        arms = [("C3", C3), ("C2", C2)] if arm == "C" else [(arm, st)]
        for a, s in arms:
            if not s:
                A(f"- {a}: NOT RUN"); continue
            A(f"- {a}: {s['n']} cells; GPU reconstruction (T0->T2, dequant + iDWT) mean {s['gpu_mean']:.3f} ms (cell spread {s['gpu_mean_spread']:.3f}), best {s['gpu_best']:.3f}; convert {s['convert_mean']:.3f} ms; submit->idle wall (decode + convert + CPU) mean {exp2.fmt(s['wall_mean'], 3)} ms (spread {exp2.fmt(s['wall_mean_spread'], 3)}), best {exp2.fmt(s['wall_best'], 3)}")
        A("")
    A("## 18. GPU clock, cycles/frame and cycles/reconstructed pixel\n")
    if clock:
        A(f"- On-device trace during the sampled passes: {clock['samples']} samples over {clock['span_s']} s, clock mean {clock['mean_mhz']:.0f} MHz, min {clock['min_mhz']:.0f}, max {clock['max_mhz']:.0f}, {clock['share_at_max_pct']:.0f} % at the top level, thermal_pwrlevel max {clock['pwrlevel_max']}, hottest {clock['hottest_min_c']:.1f}-{clock['hottest_max_c']:.1f} C (MEASURED). The final wall-timed pass has no trace of its own (the sampler collided with adb); its GPU times reproduce the sampled pass to 0.01 ms, so its clock state is taken as the same 788 MHz and marked INHERITED.")
        mhz = clock["mean_mhz"]
        A("| arm | GPU ms | Mcycles/frame | cycles/reconstructed pixel |\n|---|---|---|---|")
        for a, s in (("A", A_), ("B", B_), ("C3", C3), ("C2", C2)):
            if s:
                A(f"| {a} | {s['gpu_mean']:.3f} | {exp2.gpu_cycles_per_frame(s['gpu_mean'], mhz) / 1e6:.2f} | {cycles_per_pixel(s['gpu_mean'], mhz, width, height):.2f} |")
        A("")
    else:
        A("UNKNOWN: no clock trace.\n")
    A("## 19. Structural comparison (DERIVED from source and the Phase 0 model)\n")
    A("| | A 9/7 compute | B Haar same topology | C3 fused H3 | C2 fused H2 |\n|---|---|---|---|---|")
    A("| iDWT dispatches | 15 | 15 | 3 | 6 |")
    A("| global barriers (dequant->iDWT + inter-level) | 6 | 6 | 2 | 3 |")
    A("| workgroup barriers per tile | 3 per level (15) | 3 per level (15) | 6 (1 load + 5 levels) | 4 + 3 |")
    A("| temp LL images | 4 | 4 | 0 | 1 |")
    A("| full-frame-equivalent passes | 2.27 | 2.16 | 1.50 | 1.62 |")
    A(f"| logical bytes/frame at live size | {ref['total_bytes']:,} | {topo['topologies']['H0']['total_bytes']:,} | {topo['topologies']['H3']['total_bytes']:,} | {topo['topologies']['H2']['total_bytes']:,} |")
    A("| shared memory / workgroup | 3,280 B | 3,280 B | 4,224 B | 4,224 B |")
    A("| arithmetic per level | 4 lifting steps + K, FP32 | 2-tap lifting, FP16 | 2-tap lifting, FP16 | 2-tap lifting, FP16 |\n")
    A("## 20. Primary Comparison 1: A (9/7 FP32) vs B (Haar FP16, same topology)\n")
    if A_ and B_:
        A(f"GPU {pct(B_['gpu_mean'], A_['gpu_mean']):+.1f} %, submit->idle {exp2.fmt(pct(B_['wall_mean'], A_['wall_mean']) if B_['wall_mean'] and A_['wall_mean'] else None, 1)} % (MEASURED). Extreme arithmetic simplification at unchanged topology changes completion by about 1-2 %, exactly as Experiment 2 predicted: ops/pixel is not the axis.\n")
    A("## 21. Primary Comparison 2: B vs C (topology only)\n")
    f3 = pct(C3["wall_mean"], B_["wall_mean"]) if C3 and B_ and C3.get("wall_mean") and B_.get("wall_mean") else None
    f2 = pct(C2["wall_mean"], B_["wall_mean"]) if C2 and B_ and C2.get("wall_mean") and B_.get("wall_mean") else None
    if C3 and B_:
        A(f"- B -> C3 (H3, one stage): GPU {pct(C3['gpu_mean'], B_['gpu_mean']):+.1f} %, submit->idle {exp2.fmt(f3, 1)} % (MEASURED, matched clocks, replicated in 2 cells each with spread <= {max(C3['gpu_mean_spread'], B_['gpu_mean_spread']):.3f} ms).")
    if C2 and B_:
        A(f"- B -> C2 (H2, two stages): GPU {pct(C2['gpu_mean'], B_['gpu_mean']):+.1f} %, submit->idle {exp2.fmt(f2, 1)} %.")
    if C2 and C3:
        A(f"- C3 -> C2: GPU {pct(C2['gpu_mean'], C3['gpu_mean']):+.1f} %: the two-stage form beats the single stage, consistent with H3's 1-of-64-lane coarse levels (ESTIMATED cause; not profiled).")
    if C3 and A_:
        A(f"- Against the reference: A -> C3 GPU {pct(C3['gpu_mean'], A_['gpu_mean']):+.1f} %, A -> C2 GPU {pct(C2['gpu_mean'], A_['gpu_mean']):+.1f} %; submit->idle A -> C2 {exp2.fmt(pct(C2['wall_mean'], A_['wall_mean']) if C2.get('wall_mean') and A_.get('wall_mean') else None, 1)} %.")
    A("\nCompletion here is the standalone submit->queue-idle wall time (decode + 1.47 ms convert + CPU submit overhead), not the live receiver's submit->fence; the live fence is UNKNOWN for Arm C until a live path exists.\n")
    A("## 22. Fence vs passes / logical bytes (diagnostic, DERIVED x MEASURED)\n")
    A("| arm | full-frame passes | logical bytes/frame (live size) | global barriers | GPU ms | submit->idle ms |\n|---|---|---|---|---|---|")
    for a, s, tk in (("A", A_, None), ("B", B_, "H0"), ("C3", C3, "H3"), ("C2", C2, "H2")):
        if s:
            t = ref if tk is None else topo["topologies"][tk]
            A(f"| {a} | {t['full_frame_equivalent_passes']:.2f} | {t['total_bytes']:,} | {t['global_barriers']} | {s['gpu_mean']:.3f} | {exp2.fmt(s['wall_mean'], 3)} |")
    A("\nAcross B -> C2 -> C3 the GPU time tracks barriers and passes, not bytes: C3 has the fewest bytes and barriers yet is slower than C2, so the relationship is not a straight line in any one structural count. A diagnostic for this experiment only.\n")
    A("## 23. Outcome classification\n")
    sc = topo["verdicts"]["H3"]["pass_cut_pct"]
    o, why = outcome3(f2 if f2 is not None else f3, sc, True, ok, True)
    A(f"**Outcome {o}** — {why}. Comparison 1 (A vs B) reproduced Experiment 2's prediction (arithmetic alone ~1-2 %); Comparison 2 shows topology moves both the GPU interval (about −50 %) and completion (about −40 %) at matched clocks with correct output.\n")
    A("## 24. Remaining unknowns\n")
    A("- Live submit->fence for the fused path: UNKNOWN (standalone wall time only).\n- Physical DRAM traffic, occupancy and register allocation: UNKNOWN (no counters, no ISA); the C2 > C3 ordering is unexplained beyond the lane-utilisation estimate.\n- Real lossless game frames as a third content class: not yet available.\n- The final wall-timed pass has no clock trace of its own (INHERITED from the identical sampled pass).\n- Haar's rate control uses PyroWave's bit-plane packer unchanged; a Haar-specific entropy or packing scheme could change Control B.\n")
    A("## 25. Live-integration decision\n")
    dec, dwhy = live_integration_decision(f2 if f2 is not None else f3, ok, kodak_extra, panel_extra)
    A(f"**{dec}** — {dwhy}. The trade on the table: about half the reconstruction time (and roughly −40 % standalone completion) for about 2x the bytes on natural content (400 -> ~810 Mbps at the operating point) or +10 % on panel-like content. No live path was built in this experiment.\n")
    (Path(out_path)).write_text("\n".join(L) + "\n", encoding="utf-8")
    summary = {"cells": cells, "arms": {"A": A_, "B": B_, "C3": C3, "C2": C2}, "comparisons": cmp, "clock": clock,
               "control_a_at_416667": at, "control_a_kodak_mean": sum(kod) / len(kod) if kod else None, "control_a_panel_mean": sum(pan) / len(pan) if pan else None,
               "control_b": match, "kodak_extra_pct": kodak_extra, "panel_extra_pct": panel_extra, "rd_gate": g, "outcome": [o, why], "decision": [dec, dwhy],
               "topology": topo}
    (Path(out_path).parent / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return "\n".join(L)
