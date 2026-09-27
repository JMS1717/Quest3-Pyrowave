import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import plan as pl


def test_default_plan_is_valid_and_short_enough_to_wear():
    segments = pl.default_plan()
    pl.validate(segments)
    assert all(s.minutes() <= 2.1 for s in segments)
    assert pl.estimate_minutes(segments) <= 20


def test_default_plan_labels_are_unique_and_short():
    labels = [s.label for s in pl.default_plan()]
    assert len(labels) == len(set(labels))
    assert all(len(label) <= 12 for label in labels)


def test_default_plan_is_bitrate_ladder_over_wifi_and_rndis():
    segments = pl.default_plan()
    assert all(s.stack == "alvr" and s.codec == "H264" and s.foveation and s.eye == 2560 for s in segments)
    grid = {(s.transport, s.mbps) for s in segments}
    assert grid == {(t, m) for t in ("wifi", "rndis") for m in (400, 500, 600, 800)}


def test_default_plan_fits_the_eye_monitor_window():
    # Galaxy XR sleeps 90 s after eyes are lost (sleep warning at 60 s); with the wear sensor
    # covered, everything from the eye-timer reset to the last capture must end well before 60 s.
    for s in pl.default_plan():
        assert pl.seconds_awake_needed(s) <= 55, s.label


def test_default_plan_groups_runs_by_transport_to_minimize_switching():
    transports = [s.transport for s in pl.default_plan()]
    assert transports == sorted(transports, key=["wifi", "rndis"].index)


def test_unknown_transport_rejected_but_adb_is_known():
    pl.validate([pl.Segment("T0", "alvr", transport="adb")])


def test_unknown_transport_rejected():
    with pytest.raises(ValueError, match="transport"):
        pl.validate([pl.Segment("T1", "alvr", transport="ncm")])


def test_vd_plan_is_available_separately():
    assert [s.stack for s in pl.vd_plan()] == ["vd", "vd"]


def test_h264_frame_over_nvenc_limit_is_rejected():
    bad = pl.Segment("X1", "alvr", codec="H264", mbps=800, eye=3400)
    with pytest.raises(ValueError, match="4096x2048"):
        pl.validate([bad])


def test_unfoveated_h264_at_2560_is_rejected():
    bad = pl.Segment("X2", "alvr", codec="H264", mbps=400, eye=2560, foveation=False)
    with pytest.raises(ValueError, match="4096x2048"):
        pl.validate([bad])


def test_bitrate_over_1200_is_rejected():
    with pytest.raises(ValueError, match="1200"):
        pl.validate([pl.Segment("X3", "alvr", codec="H264", mbps=1300)])


def test_hevc_is_not_limited_to_h264_frame_size():
    pl.validate([pl.Segment("X4", "alvr", codec="Hevc", mbps=375, eye=2560, foveation=False)])


def test_duplicate_labels_rejected():
    s = pl.Segment("D", "alvr")
    with pytest.raises(ValueError, match="duplicate"):
        pl.validate([s, s])


def test_configure_args_match_the_powershell_parameters():
    args = pl.Segment("A1", "alvr", codec="H264", mbps=800, eye=2880, hz=90,
                      buffering=1.0).configure_args()
    assert args == ["-Codec", "H264", "-Mbps", "800", "-EyeSize", "2880", "-EyeHeight", "2880",
                    "-RefreshHz", "90",
                    "-MaxBufferingFrames", "1.0", "-FramePacing", "on",
                    "-Upscaling", "off", "-UpscaleFactor", "1.5", "-EdgeSharpness", "2.0",
                    "-NvencPreset", "1", "-Protocol", "Tcp",
                    "-Foveation", "on",
                    "-FoveationCenterX", "0.45", "-FoveationCenterY", "0.4",
                    "-FoveationEdgeX", "3.0", "-FoveationEdgeY", "4.0",
                    "-PacketSize", "1400",
                    "-PyroTransport", "Udp", "-Wavelet", "97", "-DecodePath", "auto",
                    "-FollowGaze", "on"]


def test_plan_round_trips_through_json(tmp_path):
    path = tmp_path / "plan.json"
    pl.save(pl.default_plan(), path)
    assert pl.load(path) == pl.default_plan()


def test_adb_plan_is_the_same_ladder_over_the_adb_tunnel():
    segments = pl.adb_plan()
    assert [(s.transport, s.mbps) for s in segments] == [("adb", m) for m in (400, 500, 600, 800)]
    pl.validate(segments)


def test_packet_size_plan_varies_only_packet_size_and_transport():
    segments = pl.packet_plan()
    pl.validate(segments)
    assert {(s.transport, s.mbps, s.packet_size) for s in segments} >= {
        ("adb", 800, 16384), ("adb", 800, 65000), ("adb", 600, 65000), ("wifi", 800, 65000), ("wifi", 500, 1400)}
    assert all(s.codec == "H264" and s.eye == 2560 for s in segments)


def test_packet_size_out_of_range_rejected():
    with pytest.raises(ValueError, match="packet"):
        pl.validate([pl.Segment("P0", "alvr", packet_size=200000)])


def test_decoder_plan_ab_tests_low_latency_decoder_on_wifi():
    segments = pl.decoder_plan()
    pl.validate(segments)
    named = [s for s in segments if s.decoder]
    assert named and all(s.decoder == "c2.qti.avc.decoder.low_latency" and s.low_latency for s in named)
    assert {s.mbps for s in segments if not s.decoder and not s.low_latency} == {400, 800}
    args = named[0].configure_args()
    assert args[-3:] == ["c2.qti.avc.decoder.low_latency", "-LowLatency", "1"]


def test_newest_valid_session_backup_skips_truncated_files(tmp_path):
    import json, os, time
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from xrbench import sweep
    good_old = tmp_path / "alvr2013-session-1.json"; good_old.write_text(json.dumps({"a": 1}))
    good_new = tmp_path / "alvr2013-session-2.json"; good_new.write_text(json.dumps({"a": 2}))
    broken = tmp_path / "alvr2013-session-3.json"; broken.write_text("")
    for i, p in enumerate((good_old, good_new, broken)):
        os.utime(p, (time.time() + i, time.time() + i))
    assert sweep.newest_valid_json(sorted(tmp_path.glob("alvr2013-session-*.json"))) == good_new
    assert sweep.is_valid_json(broken) is False and sweep.is_valid_json(good_old) is True


def test_restore_relaunches_the_alvr_client_even_without_a_headset(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from xrbench import sweep
    calls = []
    monkeypatch.setattr(sweep.subprocess, "run", lambda *a, **k: calls.append(("run", a[0])))
    monkeypatch.setattr(sweep, "log", lambda *a: None)
    monkeypatch.setattr(sweep, "relaunch_client", lambda: calls.append(("relaunch",)))
    sweep.restore(tmp_path)                      # no backups: only the SteamVR restart and the relaunch
    assert calls[-1] == ("relaunch",)

    def offline():
        raise RuntimeError("adb: device not found")
    monkeypatch.setattr(sweep, "relaunch_client", offline)
    sweep.restore(tmp_path)                      # must not raise: cleanup runs in a finally block


def test_unfoveated_plan_is_valid_and_fits_the_eye_monitor_window():
    segments = pl.unfoveated_plan()
    pl.validate(segments)
    labels = [s.label for s in segments]
    assert len(labels) == len(set(labels))
    assert all(len(label) <= 12 for label in labels)
    for s in segments:
        assert pl.seconds_awake_needed(s) <= 55, s.label


def test_unfoveated_plan_is_wifi_only_with_foveation_off():
    segments = pl.unfoveated_plan()
    assert all(s.stack == "alvr" and s.transport == "wifi" and not s.foveation for s in segments)


def test_unfoveated_plan_pairs_each_codec_with_its_low_latency_decoder():
    for s in pl.unfoveated_plan():
        assert s.low_latency, s.label
        assert s.decoder == pl.QTI_LOW_LATENCY_DECODER[s.codec], s.label


def test_unfoveated_plan_covers_three_tracks_over_one_shared_ladder():
    segments = pl.unfoveated_plan()
    tracks = {(s.codec, s.eye) for s in segments}
    # H.264 cannot reach 2560/eye unfoveated, so HEVC at 2048 is the control cell that
    # separates codec from encoded resolution.
    assert tracks == {("Hevc", 2560), ("Hevc", 2048), ("H264", 2048)}
    ladders = {t: sorted(s.mbps for s in segments if (s.codec, s.eye) == t) for t in tracks}
    assert len(set(map(tuple, ladders.values()))) == 1, "tracks must share one bitrate ladder"


def test_unfoveated_plan_descends_bitrate_comparing_codecs_at_each_step():
    segments = pl.unfoveated_plan()
    steps = []
    for s in segments:
        if not steps or steps[-1] != s.mbps:
            steps.append(s.mbps)
    assert steps == sorted(set(steps), reverse=True), "bitrate must descend, top down"


def test_foveated_plan_is_valid_and_fits_the_eye_monitor_window():
    segments = pl.foveated_plan()
    pl.validate(segments)
    labels = [s.label for s in segments]
    assert len(labels) == len(set(labels))
    assert all(len(label) <= 12 for label in labels)
    for s in segments:
        assert pl.seconds_awake_needed(s) <= 55, s.label


def test_foveated_plan_compares_codecs_at_one_resolution():
    segments = pl.foveated_plan()
    assert all(s.foveation and s.transport == "wifi" and s.eye == 2560 for s in segments)
    # foveation shrinks the encode below NVENC's 4096 width, so H.264 reaches 2560/eye here --
    # which it cannot do unfoveated. Codec is then the only variable.
    assert {s.codec for s in segments} == {"Hevc", "H264"}
    ladders = {c: sorted(s.mbps for s in segments if s.codec == c) for c in ("Hevc", "H264")}
    assert ladders["Hevc"] == ladders["H264"]


def test_foveated_plan_pairs_each_codec_with_its_low_latency_decoder():
    for s in pl.foveated_plan():
        assert s.low_latency and s.decoder == pl.QTI_LOW_LATENCY_DECODER[s.codec], s.label


def test_foveated_plan_descends_bitrate():
    steps = []
    for s in pl.foveated_plan():
        if not steps or steps[-1] != s.mbps:
            steps.append(s.mbps)
    assert steps == sorted(set(steps), reverse=True)


# --- workload classes and quality presets (2e) ---


def test_new_axes_default_to_todays_behaviour():
    # every existing plan and plan JSON must keep working untouched, so the new fields default to
    # exactly what the harness did before them
    s = pl.Segment("X", "alvr")
    assert (s.center_size_x, s.center_size_y) == (0.45, 0.40)
    assert (s.edge_ratio_x, s.edge_ratio_y) == (3.0, 4.0)
    assert s.gaze is True
    assert s.eye_h == 0          # 0 means "square, use eye"
    assert s.render_scale == 1.0
    assert s.workload == "control"


def test_eye_h_zero_means_square():
    assert pl.Segment("X", "alvr", eye=2560).eye_size() == (2560, 2560)
    assert pl.Segment("X", "alvr", eye=3520, eye_h=3840).eye_size() == (3520, 3840)


def test_configure_args_carries_the_foveation_shape():
    args = pl.Segment("X", "alvr", center_size_x=0.25, center_size_y=0.22,
                      edge_ratio_x=3.0, edge_ratio_y=4.0).configure_args()
    # scalars, not "x,y": through -File PowerShell hands a comma string over whole and refuses to
    # convert it to [double[]] -- this cost a run to learn
    assert args[args.index("-FoveationCenterX") + 1] == "0.25"
    assert args[args.index("-FoveationCenterY") + 1] == "0.22"
    assert args[args.index("-FoveationEdgeX") + 1] == "3.0"


def test_configure_args_emits_eye_height_for_non_square():
    square = pl.Segment("X", "alvr", eye=2560).configure_args()
    tall = pl.Segment("X", "alvr", eye=3520, eye_h=3840).configure_args()
    assert square[square.index("-EyeSize") + 1] == "2560"
    assert square[square.index("-EyeHeight") + 1] == "2560"
    assert tall[tall.index("-EyeSize") + 1] == "3520"
    assert tall[tall.index("-EyeHeight") + 1] == "3840"


def test_validate_uses_the_segments_own_foveation_shape_not_the_default():
    # a tight centre encodes *smaller*, so a resolution that would fail at the default centre can
    # be legal here. Validating against FoveationConfig() would reject it wrongly.
    tight = pl.Segment("X", "alvr", eye=3520, eye_h=3840, center_size_x=0.20, center_size_y=0.18)
    pl.validate([tight])
    with pytest.raises(ValueError, match="encodes larger"):
        pl.validate([pl.Segment("Y", "alvr", eye=3520, eye_h=3840)])   # default 0.45 centre


def test_quality_plan_is_valid_and_labelled_by_workload_class():
    segments = pl.quality_plan()
    pl.validate(segments)
    classes = {s.workload for s in segments}
    assert classes == {"control", "full", "seated", "seated_quality", "screen"}
    assert all(s.stack == "alvr" and s.codec == "H264" for s in segments)


def test_quality_plan_holds_the_measured_decode_budget():
    # 2.45 Mpx/eye is measured, not extrapolated: xrbench-runs/refresh2 ran panel native at centre
    # 0.20 -- 1664x1472 -- at 71.98 fps, 15.88 ms decode, 0 packets lost. Every preset must stay
    # inside the largest encoded frame this headset is known to sustain at 72 Hz. The old budget
    # was 2.30 Mpx scaled by frame period, which was a guess about 60 Hz that nothing now uses.
    from alvr_ffe_calc import FoveationConfig, encoded_eye_size
    for s in pl.quality_plan():
        cfg = FoveationConfig(center_size_x=s.center_size_x, center_size_y=s.center_size_y,
                              edge_ratio_x=s.edge_ratio_x, edge_ratio_y=s.edge_ratio_y)
        w, h = s.eye_size()
        ew, eh = encoded_eye_size(w, h, cfg if s.foveation else None)
        assert ew * eh <= pl.DECODE_BUDGET_PX, f"{s.label}: {ew*eh/1e6:.2f} Mpx over budget"


def test_the_decode_budget_is_the_cell_that_was_actually_measured():
    # guard against the budget drifting upward to accommodate a preset someone wanted: it may only
    # move when a larger frame has been measured holding 72 fps. It last moved because
    # resolution_plan measured 1792x1600 at 71.98 fps, fps_min 36.00, 0 packets lost.
    assert pl.DECODE_BUDGET_PX == 1792 * 1600


def test_quality_plan_control_is_todays_proven_config():
    control = next(s for s in pl.quality_plan() if s.label == "Q-CONTROL")
    assert (control.hz, control.mbps, control.eye, control.eye_h) == (72, 400, 2560, 0)
    assert (control.center_size_x, control.center_size_y) == (0.45, 0.40)


def test_quality_plan_pairs_each_best_with_a_gaze_off_arm():
    labels = {s.label for s in pl.quality_plan()}
    for base in ("Q-SCREEN-BEST", "Q-SQ-BEST"):
        assert base in labels and f"{base}-NG" in labels
    off = [s for s in pl.quality_plan() if s.label.endswith("-NG")]
    assert off and all(s.gaze is False for s in off)
    # the gaze-off arm must be identical apart from the gaze flag, or it is not an A/B
    for s in off:
        base = next(b for b in pl.quality_plan() if b.label == s.label[:-3])
        assert s.eye_size() == base.eye_size() and s.hz == base.hz and s.mbps == base.mbps
        assert (s.center_size_x, s.center_size_y) == (base.center_size_x, base.center_size_y)


# --- bracketed thermal control ---


def test_thermal_plan_brackets_the_ladder_with_the_same_config():
    segments = pl.thermal_plan()
    pl.validate(segments)
    assert segments[0].label == "Q-CONTROL"
    assert segments[-1].label == "Q-CONTROL-END"
    # identical in every respect that could affect decode -- otherwise a difference between them
    # would not be attributable to heat
    first, last = segments[0], segments[-1]
    for field in ("hz", "mbps", "eye", "eye_h", "codec", "foveation",
                  "center_size_x", "center_size_y", "edge_ratio_x", "edge_ratio_y",
                  "gaze", "render_scale", "buffering", "nvenc_preset"):
        assert getattr(first, field) == getattr(last, field), field


def test_thermal_plan_runs_the_screen_ladder_in_between():
    labels = [s.label for s in pl.thermal_plan()]
    assert labels[1:-1] == [s.label for s in pl.quality_plan() if s.workload == "screen"]


def test_thermal_plan_is_short_enough_to_wear_in_one_sitting():
    assert pl.estimate_minutes(pl.thermal_plan()) <= 20


# --- attended vs automated ---


def test_segments_default_to_automated():
    # the common case: performance metrics are valid with a frozen gaze, because encoded resolution
    # derives from center_size and edge_ratio only, never from the gaze-driven shift
    assert pl.Segment("X", "alvr").requires_wearer is False


def test_quality_ladder_is_automated_but_gaze_arms_are_not():
    by_label = {s.label: s for s in pl.quality_plan()}
    assert by_label["Q-SCREEN-BEST"].requires_wearer is False
    # the gaze-off arm only means anything against a *moving* gaze
    assert by_label["Q-SCREEN-BEST-NG"].requires_wearer is True


def test_automated_and_attended_partition_a_plan():
    segments = pl.quality_plan()
    automated = pl.filter_attendance(segments, wearer_present=False)
    attended = pl.filter_attendance(segments, wearer_present=True)
    assert len(automated) + len(attended) == len(segments)
    assert all(not s.requires_wearer for s in automated)
    assert all(s.requires_wearer for s in attended)


def test_resolution_plan_sweeps_the_sharp_region_at_panel_native():
    segments = pl.resolution_plan()
    pl.validate(segments)
    # every cell renders at panel native; the axis being swept is how big the sharp region can be
    assert all(s.eye_size() == (3552, 3840) for s in segments if s.label != "Q-CONTROL")
    centres = sorted({s.center_size_x for s in segments if s.label != "Q-CONTROL"})
    assert centres == [0.15, 0.20, 0.25, 0.30, 0.35]
    # HEVC is expected to fail here, but measured rather than asserted -- two cells bracket it
    assert {s.label for s in segments if s.codec == "Hevc"} == {"R-15-HEVC", "R-35-HEVC"}
    assert all(not s.requires_wearer for s in segments)


def test_resolution_plan_stays_inside_the_nvenc_width_limit():
    # 4096 is the binding constraint at panel native: centre 0.45 would encode 4544 wide
    from alvr_ffe_calc import encoded_eye_size
    for s in (x for x in pl.resolution_plan() if x.codec == "H264"):
        w, h = s.eye_size()
        ew, _ = encoded_eye_size(w, h, s.foveation_config())
        assert ew * 2 <= pl.NVENC_H264_LIMIT[0], f"{s.label}: {ew*2} wide"


# Every planner that describes current work. `default_plan` and the other historical ladders are
# deliberately absent: they exist to keep old captures comparable, so their rates must not move.
CURRENT_PLANNERS = (pl.quality_plan, pl.resolution_plan, pl.transport_thermal_plan,
                    pl.transport_verify_plan, pl.packet_thermal_plan, pl.thermal_plan)


def test_every_current_plan_runs_at_72_hz():
    # The brief: "lets keep 90 off the benchmarks for now only 72 from here on out".
    # 60 Hz was bought to widen the decode budget; F-72 showed panel-native does not need it, so
    # the whole ladder collapses onto one operating point.
    for planner in CURRENT_PLANNERS:
        rates = sorted({s.hz for s in planner()})
        assert rates == [pl.BENCH_HZ], f"{planner.__name__} runs at {rates}"


def test_the_benchmark_rate_is_one_the_runtime_actually_offers():
    # [RATES] enumerated=[60.000004, 72.00001, 90.0]. 70 Hz does not exist here, and asking for it
    # silently snaps to 72 -- server_core/connection.rs picks the closest advertised rate and warns.
    assert pl.BENCH_HZ in (60, 72, 90)
    assert pl.BENCH_HZ == 72


def test_the_refresh_sweep_is_retired():
    # it existed to compare 60/72/90; with a single rate there is nothing left for it to vary
    assert not hasattr(pl, "refresh_plan")


def test_packet_thermal_plan_varies_only_the_shard_size():
    segments = pl.packet_thermal_plan()
    pl.validate(segments)
    assert sorted(s.packet_size for s in segments) == [1400, 8192, 32768, 65000]
    # everything that could otherwise explain a temperature difference is held fixed
    for field in ("mbps", "hz", "eye", "eye_h", "codec", "center_size_x", "edge_ratio_x"):
        assert len({getattr(s, field) for s in segments}) == 1, field
    assert all(not s.requires_wearer for s in segments)


def test_transport_thermal_plan_changes_one_variable_at_a_time():
    segments = {s.label: s for s in pl.transport_thermal_plan()}
    pl.validate(list(segments.values()))
    base, shards, udp = segments["P-TCP-1400"], segments["P-TCP-65000"], segments["P-UDP-1400"]
    # shard-size arm differs from baseline only in packet_size
    assert (shards.protocol, shards.packet_size) == ("Tcp", 65000)
    assert base.protocol == shards.protocol
    # protocol arm differs only in protocol -- UDP stays at 1400, since larger fragments at the IP
    # layer and would add back the per-packet cost the test is removing
    assert (udp.protocol, udp.packet_size) == ("Udp", 1400)
    assert base.packet_size == udp.packet_size
    for field in ("mbps", "hz", "eye", "eye_h", "center_size_x"):
        assert len({getattr(s, field) for s in segments.values()}) == 1, field


def test_new_plans_use_the_measured_shard_size_but_historical_ones_do_not():
    # 65000-byte TCP shards removed the frame drops and the CPU thermal rise entirely
    assert pl.TCP_SHARD_BYTES == 65000
    for planner in (pl.quality_plan, pl.resolution_plan, pl.thermal_plan):
        alvr = [s for s in planner() if s.workload != "control"]
        assert alvr and all(s.packet_size == pl.TCP_SHARD_BYTES for s in alvr), planner.__name__
        # the control must reproduce today's proven config exactly, including the 1400 default --
        # "improving" it would destroy the only fixed reference the ladder has
        for control in (s for s in planner() if s.workload == "control"):
            assert control.packet_size == 1400, planner.__name__
    # the historical ladder keeps 1400 so the 45-segment baseline stays comparable
    assert all(s.packet_size == 1400 for s in pl.default_plan())


def test_transport_verify_reverses_the_order_of_the_two_tcp_cells():
    labels = [s.label for s in pl.transport_verify_plan()]
    # 65000 goes first this time, so it gets the cold start the baseline had before
    assert labels == ["V-TCP-65000", "V-TCP-1400"]
    segments = pl.transport_verify_plan()
    pl.validate(segments)
    assert all(s.protocol == "Tcp" for s in segments)
    for field in ("mbps", "hz", "eye", "eye_h", "center_size_x"):
        assert len({getattr(s, field) for s in segments}) == 1, field


# --- link_plan: USB vs Wi-Fi at both geometries ---


def test_link_plan_crosses_every_transport_with_geometry():
    segments = [s for s in pl.link_plan() if s.label != "Q-CONTROL"]
    pl.validate(pl.link_plan())
    combos = {(s.transport, s.eye_size()) for s in segments}
    assert combos == {(t, g) for t in ("wifi", "rndis", "adb")
                      for g in ((3552, 3840), (2560, 2560))}


def test_link_plan_includes_the_adb_tunnel():
    # measured: the USB-C bus moves 3739 Mbps PC->headset by raw bulk transfer, while
    # RNDIS delivers 453. adb forward tunnels TCP over those same bulk endpoints, so it is the
    # cheapest test of whether the loss is RNDIS rather than the cable -- and it needs no new code
    assert any(s.transport == "adb" for s in pl.link_plan())


def test_link_plan_holds_everything_but_transport_and_geometry_fixed():
    segments = [s for s in pl.link_plan() if s.label != "Q-CONTROL"]
    for field in ("hz", "mbps", "codec", "packet_size", "protocol", "gaze", "render_scale"):
        assert len({getattr(s, field) for s in segments}) == 1, field


def test_link_plan_alternates_transport_so_heat_does_not_track_it():
    # the shard-size run was confounded exactly this way: a cell that started 24 C cooler "rose"
    # 24 C and one that started hot "rose" 2 C, and both ended at the same temperature. Alternating
    # means thermal drift across the sitting cannot masquerade as a transport difference.
    order = [s.transport for s in pl.link_plan() if s.label != "Q-CONTROL"]
    assert all(a != b for a, b in zip(order, order[1:])), order


def test_link_plan_is_automated():
    assert all(not s.requires_wearer for s in pl.link_plan())


# --- buffering: the largest single term in the latency budget ---


def test_buffering_plan_varies_only_the_buffer_depth():
    segments = [s for s in pl.buffering_plan() if s.label != "Q-CONTROL"]
    pl.validate(pl.buffering_plan())
    assert sorted(s.buffering for s in segments) == [1.0, 1.25, 1.5, 2.0]
    # anything else moving would confound the one measurement this plan exists to make
    for field in ("hz", "mbps", "eye", "eye_h", "codec", "center_size_x", "packet_size",
                  "transport", "gaze", "render_scale"):
        assert len({getattr(s, field) for s in segments}) == 1, field


def test_buffering_plan_includes_todays_default_as_its_own_reference():
    # 2.0 is what every measurement so far used; without it in the same sitting the comparison
    # would be against runs taken at a different temperature
    assert 2.0 in {s.buffering for s in pl.buffering_plan()}


def test_buffering_plan_respects_alvrs_floor():
    # settings.rs: gui(slider(min = 1.0, max = 10.0)). Below 1.0 is not a supported value and
    # would be silently clamped, producing a duplicate cell wearing a different label.
    assert min(s.buffering for s in pl.buffering_plan()) >= 1.0


def test_buffering_plan_is_automated():
    assert all(not s.requires_wearer for s in pl.buffering_plan())


# --- frame pacing: the second queuing lever ---


def test_segments_pace_frames_by_default():
    # matches ALVR's own default (settings.rs enforce_server_frame_pacing: true), so every
    # measurement taken before this axis existed stays comparable
    assert pl.Segment("X", "alvr").frame_pacing is True


def test_configure_args_carry_frame_pacing():
    on = pl.Segment("X", "alvr").configure_args()
    assert on[on.index("-FramePacing") + 1] == "on"
    off = pl.Segment("X", "alvr", frame_pacing=False).configure_args()
    assert off[off.index("-FramePacing") + 1] == "off"


def test_pacing_plan_is_a_two_by_two_of_both_queuing_levers():
    segments = [s for s in pl.pacing_plan() if s.label != "Q-CONTROL"]
    pl.validate(pl.pacing_plan())
    combos = {(s.frame_pacing, s.buffering) for s in segments}
    assert combos == {(True, 2.0), (False, 2.0), (True, 1.0), (False, 1.0)}


def test_pacing_plan_includes_todays_defaults_as_its_reference_cell():
    # pacing on at 2.0 frames is exactly what every run so far used; measuring it in the same
    # sitting is what makes the other three cells comparable
    assert (True, 2.0) in {(s.frame_pacing, s.buffering) for s in pl.pacing_plan()}


def test_pacing_plan_holds_geometry_fixed():
    segments = [s for s in pl.pacing_plan() if s.label != "Q-CONTROL"]
    for field in ("hz", "mbps", "eye", "eye_h", "codec", "center_size_x", "packet_size",
                  "transport"):
        assert len({getattr(s, field) for s in segments}) == 1, field


def test_pacing_plan_is_automated():
    assert all(not s.requires_wearer for s in pl.pacing_plan())


def test_quality_ladder_uses_the_measured_buffer_depth():
    # 1.5 frames measured best on every axis at once (buffering1): 89.26 ms against
    # 104.53 at the 2.0 default, and fps_min 71.58 against 36.0. Going lower backfired -- 1.0 gave
    # 98.44 ms and fps_min 4.8, because the wait relocates into vsync_queue and frames start
    # missing their deadline.
    ladder = [s for s in pl.quality_plan() if s.workload != "control"]
    assert ladder and all(s.buffering == 1.5 for s in ladder)


def test_the_control_keeps_alvrs_default_buffer():
    # same reasoning as the shard size: the control reproduces the measured reference exactly, so
    # an improvement must not reach it
    control = next(s for s in pl.quality_plan() if s.workload == "control")
    assert control.buffering == 2.0


# --- pacing headroom: a bounded version of turning frame pacing off ---


def test_segments_have_no_pacing_headroom_by_default():
    # 0 is exactly today's behaviour, so every plan and capture predating the axis is unaffected
    assert pl.Segment("X", "alvr").pacing_headroom_us == 0


def test_pacing_headroom_plan_sweeps_it_against_a_zero_reference():
    segments = [s for s in pl.headroom_plan() if s.label != "Q-CONTROL"]
    pl.validate(pl.headroom_plan())
    values = sorted(s.pacing_headroom_us for s in segments)
    assert values[0] == 0, "needs a zero cell or there is nothing to compare against"
    assert values == [0, 2000, 4000, 6000]


def test_pacing_headroom_stays_well_inside_a_frame_period():
    # the guard that matters: past some fraction of the frame period frames start arriving before
    # the previous one is consumed and the queue grows again, which is what pacing prevents
    period_us = 1_000_000 / pl.BENCH_HZ
    for s in pl.headroom_plan():
        assert s.pacing_headroom_us < period_us / 2, s.label


def test_pacing_headroom_plan_holds_everything_else_fixed():
    segments = [s for s in pl.headroom_plan() if s.label != "Q-CONTROL"]
    for field in ("hz", "mbps", "eye", "eye_h", "codec", "buffering", "center_size_x",
                  "packet_size", "frame_pacing"):
        assert len({getattr(s, field) for s in segments}) == 1, field


# --- SGSR upscaling: already in the shader, off by default ---


def test_segments_have_upscaling_off_by_default():
    # matches ALVR's own default (settings.rs upscaling.enabled: false), so every plan and capture
    # predating this axis stays comparable
    s = pl.Segment("X", "alvr")
    assert s.upscaling is False
    assert (s.upscale_factor, s.edge_sharpness) == (1.5, 2.0)


def test_configure_args_carry_the_upscaling_shape():
    args = pl.Segment("X", "alvr", upscaling=True, upscale_factor=1.25,
                      edge_sharpness=1.8).configure_args()
    assert args[args.index("-Upscaling") + 1] == "on"
    assert args[args.index("-UpscaleFactor") + 1] == "1.25"
    assert args[args.index("-EdgeSharpness") + 1] == "1.8"
    assert pl.Segment("X", "alvr").configure_args()[
        pl.Segment("X", "alvr").configure_args().index("-Upscaling") + 1] == "off"


def test_upscaling_plan_sweeps_the_factor_against_an_off_reference():
    segments = [s for s in pl.upscaling_plan() if s.label != "Q-CONTROL"]
    pl.validate(pl.upscaling_plan())
    assert any(not s.upscaling for s in segments), "needs an off cell to compare against"
    factors = sorted(s.upscale_factor for s in segments if s.upscaling)
    assert factors == [1.25, 1.5]


def test_upscaling_plan_holds_the_encoded_frame_fixed():
    # the whole point: upscaling costs client GPU, not decode. If geometry moved between arms the
    # comparison would be buying decode cost rather than free sharpening.
    segments = [s for s in pl.upscaling_plan() if s.label != "Q-CONTROL"]
    for field in ("hz", "mbps", "eye", "eye_h", "codec", "center_size_x", "buffering",
                  "packet_size"):
        assert len({getattr(s, field) for s in segments}) == 1, field


def test_upscaling_plan_is_automated():
    assert all(not s.requires_wearer for s in pl.upscaling_plan())


# ---- PyroWave as a segment, so codec comparisons run under one protocol ----
# The first PyroWave numbers came from ad-hoc scripts: no preflight, no cool-down,
# the capture script live on the PC, VLC live on the headset. A codec is a segment axis like any
# other; the harness's preflight, thermals and cool-down then apply to both arms equally.

def test_pyro_plan_is_a_rising_bitrate_ladder_at_one_geometry():
    # Owner's call: start at 100 Mbps and climb to 600, one segment per step, so the
    # first cells run coolest and a cliff shows up as the step where fps or decode gives way.
    segs = pl.pyro_plan()
    pl.validate(segs)
    assert [s.codec for s in segs] == ["PyroWave"] * 6
    assert [s.mbps for s in segs] == [100, 200, 300, 400, 500, 600]
    assert [s.label for s in segs] == ["P-100", "P-200", "P-300", "P-400", "P-500", "P-600"]
    base = next(s for s in pl.buffering_plan() if s.label == "B-15")
    for s in segs:
        for f in ("eye", "eye_h", "hz", "buffering", "center_size_x", "center_size_y",
                  "packet_size", "transport", "foveation"):
            assert getattr(s, f) == getattr(base, f), f


def test_pyro_latestart_plan_moves_only_the_frame_start():
    segs = pl.pyro_latestart_plan()
    pl.validate(segs)
    assert [s.pacing_delay_us for s in segs] == [0, 4000, 8000, 12000, 16000, 20000]
    assert [s.label for s in segs] == ["PL-0", "PL-4", "PL-8", "PL-12", "PL-16", "PL-20"]
    base = next(s for s in pl.pyro_plan() if s.mbps == 200)
    for s in segs:
        assert s.codec == "PyroWave" and s.mbps == 200 and s.hz == 72
        for f in ("eye", "eye_h", "buffering", "center_size_x", "center_size_y", "packet_size",
                  "pacing_headroom_us", "frame_pacing"):
            assert getattr(s, f) == getattr(base, f), f


def test_pyro_phase_plan_interleaves_lock_on_and_off():
    segs = pl.pyro_phase_plan()
    pl.validate(segs)
    assert [s.label for s in segs] == ["PP-OFF-1", "PP-ON-1", "PP-OFF-2", "PP-ON-2"]
    assert [s.phase_lock for s in segs] == [False, True, False, True]
    base = next(s for s in pl.pyro_latestart_plan() if s.pacing_delay_us == 0)
    for s in segs:
        assert s.pacing_delay_us == 0 and s.codec == "PyroWave" and s.mbps == base.mbps
    assert pl.Segment("x", "alvr").phase_lock is False, "default keeps every existing plan unchanged"


def test_pyro_earlypoll_plan_interleaves_the_loop_order():
    segs = pl.pyro_earlypoll_plan()
    pl.validate(segs)
    assert [s.label for s in segs] == ["PE-OFF-1", "PE-ON-1", "PE-OFF-2", "PE-ON-2"]
    assert [s.early_poll for s in segs] == [False, True, False, True]
    assert all(not s.phase_lock and s.pacing_delay_us == 0 for s in segs)
    assert pl.Segment("x", "alvr").early_poll is True, "matches the patched client's default"


def test_pyro_hz_plan_interleaves_72_and_90_at_two_bitrates():
    segs = pl.pyro_hz_plan()
    pl.validate(segs)
    assert [s.label for s in segs] == ["H72-100", "H90-100", "H72-200", "H90-200"]
    assert [(s.hz, s.mbps) for s in segs] == [(72, 100), (90, 100), (72, 200), (90, 200)]
    assert all(s.codec == "PyroWave" and s.eye == 3552 for s in segs)


def test_pyro_res_plan_scales_the_panel_linearly():
    segs = pl.pyro_res_plan()
    pl.validate(segs)
    assert [s.label for s in segs] == ["X60-600", "X60-600-90", "X60-400-90", "X60-300-90", "X70-600", "X70-600-90", "S60-400-90"]
    sustained = segs[-1]
    assert sustained.scene_seconds == 300 and (sustained.mbps, sustained.hz) == (400, 90)
    x = next(s for s in segs if s.label == "X60-400-90")
    assert (x.eye, x.eye_h, x.mbps, x.hz) == (2131, 2304, 400, 90)
    x60 = segs[0]
    assert (x60.eye, x60.eye_h, x60.mbps, x60.hz) == (2131, 2304, 600, 72)
    assert abs(x60.eye * x60.eye_h / (3552 * 3840) - 0.36) < 0.01
    assert segs[1].hz == 90 and (segs[4].eye, segs[4].eye_h) == (2486, 2688)


def test_pyro_segment_selects_pyrowave_in_the_session():
    # the beta build reads PyroWave from the session (video.preferred_codec and video.pyrowave),
    # not from the launcher's environment
    seg = next(s for s in pl.pyro_plan() if s.codec == "PyroWave")
    args = seg.configure_args()
    assert args[args.index("-Codec") + 1] == "PyroWave"
    assert args[args.index("-PyroTransport") + 1] == "Udp"


def test_pyro_knobs_reach_the_configurator():
    seg = pl.Segment("P", "alvr", codec="PyroWave", wavelet="53", decode_path="fragment",
                     gaze=False)
    args = seg.configure_args()
    assert args[args.index("-Wavelet") + 1] == "53"
    assert args[args.index("-DecodePath") + 1] == "fragment"
    assert args[args.index("-FollowGaze") + 1] == "off"


def test_validate_accepts_pyrowave_without_the_nvenc_limit():
    seg = pl.Segment("P", "alvr", codec="PyroWave", mbps=600, eye=3552, eye_h=3840,
                          foveation=False)
    pl.validate([seg])   # 3552x3840 unfoveated would fail H.264's NVENC limit


def test_pyro_ab_plan_interleaves_300_and_400_on_the_photo_layout():
    segs = pl.pyro_ab_plan()
    pl.validate(segs)
    assert [s.label for s in segs] == ["AB-300-1", "AB-400-1", "AB-300-2", "AB-400-2"]
    assert [s.mbps for s in segs] == [300, 400, 300, 400]
    base = next(s for s in pl.pyro_res_plan() if s.label == "X60-400-90")
    for s in segs:
        assert (s.hz, s.eye, s.eye_h, s.codec) == (90, base.eye, base.eye_h, "PyroWave")
        assert s.photo.endswith("ab_photo.png")
    assert pl.Segment("x", "alvr").photo == "", "existing plans keep the synthetic panel"


def test_pyro_live3_plan_is_the_three_chosen_cells():
    segs = pl.pyro_live3_plan()
    pl.validate(segs)
    assert [(s.label, s.eye, s.eye_h, s.mbps, s.hz) for s in segs] == [
        ("L2560-300", 2560, 2560, 300, 90), ("L2304-300", 2304, 2304, 300, 90), ("L2304-243", 2304, 2304, 243, 90)]
    assert all(s.codec == "PyroWave" and s.photo for s in segs)


def test_pyro_path_plan_alternates_fragment_and_compute_at_the_operating_point():
    segs = pl.pyro_path_plan()
    pl.validate(segs)
    assert [(s.label, s.decode_path, s.scene_seconds) for s in segs] == [
        ("PF-1", "fragment", 33), ("PC-1", "compute", 33), ("PF-2", "fragment", 33), ("PC-2", "compute", 33),
        ("SF", "fragment", 300), ("SC", "compute", 300)]
    base = next(s for s in pl.pyro_res_plan() if s.label == "X60-400-90")
    for s in segs:
        assert (s.eye, s.eye_h, s.mbps, s.hz, s.codec) == (base.eye, base.eye_h, base.mbps, base.hz, "PyroWave")
    assert pl.Segment("x", "alvr").decode_path == "auto"


def test_pyro_53_plan_pairs_97_and_53_on_the_compute_path_at_the_operating_point():
    segs = pl.pyro_53_plan()
    pl.validate(segs)
    assert [(s.label, s.wavelet, s.scene_seconds) for s in segs] == [
        ("P97-1", "97", 33), ("P53-1", "53", 33), ("P97-2", "97", 33), ("P53-2", "53", 33),
        ("S97", "97", 300), ("S53", "53", 300)]
    base = next(s for s in pl.pyro_res_plan() if s.label == "X60-400-90")
    for s in segs:
        assert (s.eye, s.eye_h, s.mbps, s.hz, s.codec) == (base.eye, base.eye_h, base.mbps, base.hz, "PyroWave")
        # Both arms on the compute path: Experiment 1 made it the baseline and 5/3 exists only there.
        assert s.decode_path == "compute"
    # 9/7 keeps PyroWave's default precision (FP32 math, FP16 storage on two levels); 5/3 runs
    # all-FP16 (precision 0), which is the experiment's second variable by design.
    assert [s.pyro_precision for s in segs] == [1, 0, 1, 0, 1, 0]
    default = pl.Segment("x", "alvr")
    assert (default.wavelet, default.pyro_precision) == ("97", -1)


def test_segment_rejects_an_unknown_wavelet_or_precision():
    with pytest.raises(ValueError):
        pl.validate([pl.Segment("w", "alvr", codec="PyroWave", wavelet="44")])
    with pytest.raises(ValueError):
        pl.validate([pl.Segment("p", "alvr", codec="PyroWave", pyro_precision=3)])


def test_corpus_plan_dumps_clips_per_class_at_the_operating_point():
    segs = pl.corpus_plan()
    pl.validate(segs)
    assert [(s.label, s.dump_label, s.dump_frames) for s in segs] == [
        ("CP-PANEL", "synthetic_panel", 90), ("CP-PHOTO", "textures_photo", 90), ("CP-HOME", "steamvr_home", 90)]
    base = next(s for s in pl.pyro_res_plan() if s.label == "X60-400-90")
    for s in segs:
        assert (s.eye, s.eye_h, s.mbps, s.hz, s.codec) == (base.eye, base.eye_h, base.mbps, base.hz, "PyroWave")
    home = segs[2]
    assert home.game_seconds == 60 and home.dump_start > 90 * 33   # inside the Home phase, after the 33 s scene
    assert pl.Segment("x", "alvr").dump_frames == 0
