from xrbench import exp4


def _log(path, gpu, wall, stages=()):
    lines = [f"T2 decode      : best {gpu - 0.02:.3f} ms, mean {gpu:.3f} ms", "T3-T2 convert  : best 1.466 ms, mean 1.466 ms",
             f"submit->idle wall: best {wall - 0.1:.3f} ms, mean {wall:.3f} ms"]
    for i, (a, b, m) in enumerate(stages):
        lines.append(f"fused stage {i + 1} (levels {a}->{b}): mean {m:.3f} ms")
    path.write_text("\n".join(lines) + "\n")


def test_load_cells_and_arm_summary_average_replicates_and_stages(tmp_path):
    _log(tmp_path / "cellp4_A_1.log", 7.06, 9.0)
    _log(tmp_path / "cellp4_A_2.log", 7.08, 9.1)
    _log(tmp_path / "cellp4_N32_3.log", 19.0, 20.9, [(5, 2, 2.7), (2, 0, 16.3)])
    _log(tmp_path / "cellp4_N32_4.log", 19.2, 21.1, [(5, 2, 2.9), (2, 0, 16.3)])
    cells = exp4.load_cells(tmp_path, "cellp4")
    s = exp4.arm_summary(cells)
    assert s["A"]["n"] == 2 and abs(s["A"]["gpu_mean"] - 7.07) < 1e-9 and abs(s["A"]["gpu_spread"] - 0.02) < 1e-9
    assert s["N32"]["stages"] == [(5, 2, 2.8), (2, 0, 16.3)]
    assert exp4.pct(3.5, 7.0) == -50.0


def test_cell_windows_and_clock_selection(tmp_path):
    (tmp_path / "cells.txt").write_text("A 1 start 100 hottest 66700\nA 1 end 103 hottest 66800\nB 2 start 110 hottest 66700\nB 2 end 112 hottest 66900\n")
    w = exp4.load_cell_windows(tmp_path / "cells.txt")
    assert w == [("A", 1, 100, 103, 66.7, 66.8), ("B", 2, 110, 112, 66.7, 66.9)]
    (tmp_path / "clk.csv").write_text("99 788000000 50 % 0 66700\n101 788000000 50 % 0 66700\n105 421000000 5 % 0 66700\n111 788000000 60 % 0 66700\n")
    c = exp4.clock_in_windows(tmp_path / "clk.csv", w)
    assert c["samples"] == 3 and c["min_mhz"] == 788 and c["total_samples"] == 4


def test_structure_rows_carry_the_apron_penalty_for_97():
    s = exp4.structure_rows()
    assert s["N32"]["dispatches"] == 6 and s["N32"]["global_barriers"] == 3 and s["N32"]["redundant"] > 2
    assert s["F32"]["redundant"] == 1.0 and s["A"]["dispatches"] == 15
