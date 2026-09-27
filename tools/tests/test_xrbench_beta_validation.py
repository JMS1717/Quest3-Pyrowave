"""The beta validation plan: the recommended profile three times, the tuned H.264 profile once, and
each PyroWave experiment control flipped once -- all with every research override cleared, so
the stream is configured by the session alone, as a tester's would be. Home results."""
from xrbench import plan as pl
from xrbench import sweep


def cells():
    return {s.label: s for s in pl.beta_validation_plan()}


def test_the_plan_covers_both_profiles_and_every_pyrowave_toggle():
    assert sorted(cells()) == sorted(["BV-PW-1", "BV-PW-2", "BV-PW-3", "BV-H264",
                                      "BV-TCP", "BV-53", "BV-FRAG", "BV-70"])
    for seg in cells().values():
        assert seg.settings_only, seg.label
        assert seg.scene == "home", seg.label


def test_the_recommended_cells_are_the_profile():
    seg = cells()["BV-PW-1"]
    assert (seg.codec, seg.mbps, seg.hz, seg.eye, seg.eye_h) == ("PyroWave", 400, 90, 2131, 2304)
    assert (seg.pyro_transport, seg.wavelet, seg.decode_path, seg.gaze) == ("Udp", "97", "compute", True)
    assert (seg.center_size_x, seg.center_size_y, seg.edge_ratio_x, seg.edge_ratio_y) == (0.20, 0.178, 3.0, 4.0)


def test_the_h264_cell_is_the_tuned_profile():
    seg = cells()["BV-H264"]
    assert (seg.codec, seg.mbps, seg.hz, seg.eye, seg.eye_h) == ("H264", 600, 72, 3552, 3840)
    assert (seg.buffering, seg.packet_size, seg.protocol, seg.nvenc_preset) == (1.5, 65000, "Tcp", 1)


def test_each_toggle_cell_changes_one_thing():
    base = cells()["BV-PW-1"]
    assert cells()["BV-TCP"].pyro_transport == "Tcp"
    assert cells()["BV-53"].wavelet == "53"
    assert cells()["BV-FRAG"].decode_path == "fragment"
    assert (cells()["BV-70"].eye, cells()["BV-70"].eye_h) == (2486, 2688)
    for label, field in (("BV-TCP", "pyro_transport"), ("BV-53", "wavelet"), ("BV-FRAG", "decode_path")):
        assert getattr(cells()[label], field) != getattr(base, field), label


def test_settings_only_cells_clear_every_research_override():
    seg = cells()["BV-53"]
    # headset decode path property "auto" is no override; wavelet env cleared (as for 9/7); gaze env cleared
    assert sweep.research_overrides(seg) == {"decode_path_prop": "auto", "wavelet_env": "97",
                                             "gaze_env": True, "precision_prop": -1}
    assert sweep.research_overrides(cells()["BV-FRAG"])["decode_path_prop"] == "auto"


def test_research_cells_keep_their_overrides():
    seg = pl.Segment("X", "alvr", codec="PyroWave", wavelet="53", decode_path="fragment", gaze=False,
                     pyro_precision=0)
    assert sweep.research_overrides(seg) == {"decode_path_prop": "fragment", "wavelet_env": "53",
                                             "gaze_env": False, "precision_prop": 0}


def test_the_plan_is_selectable():
    assert "beta-validation" in sweep.PLAN_BUILDERS
