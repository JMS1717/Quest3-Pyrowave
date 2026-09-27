import pytest

from xrbench import exp2, exp3


def test_stage_plan_walks_from_the_coarsest_level_to_the_output():
    assert exp3.stage_plan((1, 1, 1, 1, 1)) == [(5, 4), (4, 3), (3, 2), (2, 1), (1, 0)]
    assert exp3.stage_plan((3, 2)) == [(5, 2), (2, 0)]
    assert exp3.stage_plan((5,)) == [(5, 0)]
    with pytest.raises(ValueError):
        exp3.stage_plan((2, 2))


def test_h0_reproduces_the_single_level_byte_model_at_precision_0():
    t = exp3.topology("H0", (1, 1, 1, 1, 1), 1984, 896, True, 0)
    lb = exp2.logical_bytes(1984, 896, True, 0)
    assert t["total_bytes"] == lb["total_bytes"]
    assert t["dispatches"] == 15 and t["global_barriers"] == 6 and t["temp_global_buffers"] == 4


def test_h3_removes_every_ll_round_trip_and_barrier():
    t = exp3.topology("H3", (5,), 64, 64, True, 0)
    assert t["dispatches"] == 3 and t["global_barriers"] == 2 and t["temp_global_buffers"] == 0
    assert t["ll_write_bytes"] == 0 and t["ll_reread_bytes"] == 0
    # luma: dequant writes 3 bands per level + LL5; reads the same set once; writes 64*64 R8
    per_comp_coeffs = sum(3 * (64 >> l) ** 2 for l in range(1, 6)) + (64 >> 5) ** 2
    assert t["coefficient_read_bytes"] == 3 * per_comp_coeffs * 2
    assert t["total_bytes"] == 3 * (per_comp_coeffs * 2 * 2 + 64 * 64)
    assert t["max_shared_bytes"] == 2 * 32 * 32 * 2   # ping-pong tiles, as haar_fused.comp allocates
    assert t["min_lane_utilisation"] == pytest.approx(1 / 64)


def test_h2_has_one_ll_round_trip():
    t = exp3.topology("H2", (3, 2), 64, 64, True, 0)
    assert t["dispatches"] == 6 and t["global_barriers"] == 3 and t["temp_global_buffers"] == 1
    n_ll2 = 3 * (64 >> 2) ** 2 * 2
    assert t["ll_write_bytes"] == n_ll2 and t["ll_reread_bytes"] == n_ll2


def test_structural_gate_requires_feasible_and_material():
    ref = exp3.reference(1984, 896)
    tops = {n: exp3.topology(n, k, 1984, 896) for n, k in exp3.TOPOLOGIES.items()}
    proceed, verdicts = exp3.structural_gate(tops, ref)
    assert "H0" not in proceed
    assert all(verdicts[n]["feasible"] for n in tops)
    # at the live encoded size H3 cuts barriers 6->2 and passes by a third: material by structure
    assert verdicts["H3"]["structure_ok"] and "H3" in proceed
    assert not verdicts["H0"]["material"]
    # a topology that fails feasibility never proceeds even when its bytes qualify
    fake = dict(tops["H3"]); fake["max_shared_bytes"] = 40 * 1024
    ok, why = exp3.feasible(fake)
    assert not ok and "shared" in why[0]


def test_rd_gate_bands():
    assert exp3.rd_gate(35.0, -1.0, 40.0)[0] == "PROCEED"
    assert exp3.rd_gate(70.0, 2.0, 40.0)[0].startswith("PROCEED")
    assert exp3.rd_gate(95.0, 0.0, 10.0)[0] == "STOP"
    assert exp3.rd_gate(60.0, 0.0, 40.0)[0] == "STOP"


def test_render_topology_writes_report_and_json(tmp_path):
    proceed, verdicts, tops, ref = exp3.render_topology(1984, 896, tmp_path)
    assert (tmp_path / "topology.md").exists() and (tmp_path / "topology.json").exists()
    assert set(verdicts) == {"H0", "H1", "H2", "H3"}


def test_parse_cell_log_and_cycles_per_pixel():
    r = exp3.parse_cell_log("T2 decode      : best 3.703 ms, mean 3.742 ms\nT3-T2 convert  : best 1.466 ms, mean 1.466 ms\nsubmit->idle wall: best 5.507 ms, mean 5.636 ms\n")
    assert r["gpu_mean"] == 3.742 and r["wall_mean"] == 5.636 and r["convert_best"] == 1.466
    assert exp3.parse_cell_log("nothing") is None
    assert exp3.cycles_per_pixel(3.742, 788.0, 3328, 1472) == pytest.approx(3.742e-3 * 788e6 / (3328 * 1472 * 3))


def test_outcome3_bands():
    assert exp3.outcome3(-40.0, 33.8, True, True, True)[0] == "A"
    assert exp3.outcome3(-7.0, 33.8, True, True, True)[0] == "B"
    assert exp3.outcome3(-2.0, 33.8, True, True, True)[0] == "C"
    assert exp3.outcome3(8.0, 33.8, True, True, True)[0] == "E"
    assert exp3.outcome3(-40.0, 33.8, False, True, True)[0] == "CONFOUNDED"
    assert exp3.outcome3(-40.0, 33.8, True, False, True)[0] == "INVALID"


def test_live_integration_decision():
    assert exp3.live_integration_decision(-40.0, True, 103.0, 10.5)[0] == "NOT NOW"
    assert exp3.live_integration_decision(-40.0, True, 30.0, 10.5)[0] == "PROCEED"
    assert exp3.live_integration_decision(-2.0, True, 30.0, 10.5)[0] == "NO"


def test_apron_topology_reproduces_haar_when_zero_and_grows_reads_for_97():
    h = exp3.topology("H2", (3, 2), 1984, 896, apron=0)
    n = exp3.topology("N32", (3, 2), 1984, 896, apron=4)
    assert n["dispatches"] == h["dispatches"] and n["global_barriers"] == h["global_barriers"]
    assert n["coefficient_read_bytes"] > h["coefficient_read_bytes"]
    # stage 1 fuses 3 levels: halo 12 output samples per side at its output level, 4x4 LL input
    # tile becomes (4 + 24/8)^2 / 16 = 3.06x
    assert n["stages"][0]["redundant_load_factor"] == pytest.approx((4 + 24 / 8) ** 2 / 16)
    one = exp3.topology("N1", (1, 1, 1, 1, 1), 1984, 896, apron=4)
    assert one["stages"][0]["redundant_load_factor"] == pytest.approx((16 + 8 / 2) ** 2 / 256)
    assert n["max_shared_bytes"] > h["max_shared_bytes"]
