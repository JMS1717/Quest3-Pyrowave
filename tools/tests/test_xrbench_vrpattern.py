import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import vrpattern as vp


def test_patches_have_the_exact_values_we_expect_back():
    image = vp.build_pattern(256, 128)
    assert image.shape == (128, 256, 4)
    for patch in vp.PATCHES:
        x, y = vp.patch_center(patch, 256, 128)
        assert tuple(image[y, x][:3]) == patch.rgb, patch.name


def test_gradient_band_is_monotonic_and_spans_the_range():
    image = vp.build_pattern(512, 256)
    row = vp.gradient_row(256)
    values = [int(image[row, x][1]) for x in range(512)]
    assert values[0] == 0 and values[-1] == 255
    assert all(b >= a for a, b in zip(values, values[1:]))


def test_expected_value_lookup_matches_the_rendered_image():
    image = vp.build_pattern(320, 160)
    for u, v in ((0.1, 0.1), (0.5, 0.5), (0.9, 0.8)):
        x, y = int(u * 320), int(v * 160)
        assert vp.expected_rgb(u, v, 320, 160) == tuple(int(c) for c in image[y, x][:3])


def test_raw_file_round_trips(tmp_path):
    path = tmp_path / "pattern.raw"
    vp.write_raw(path, 64, 32)
    data = path.read_bytes()
    assert len(data) == 64 * 32 * 4
    assert data[:4] == bytes(vp.build_pattern(64, 32)[0, 0])


def test_stereo_raw_puts_the_same_pattern_in_both_eyes(tmp_path):
    path = tmp_path / "stereo.raw"
    vp.write_stereo_raw(path, 64, 32)
    data = path.read_bytes()
    assert len(data) == 128 * 32 * 4
    row = data[:128 * 4]
    assert row[:64 * 4] == row[64 * 4:]           # left eye == right eye


def test_probe_samples_parse_from_the_client_log():
    log = ("09-19 18:20:01.1 I XRWiredXR: XRPIX grid=2 eye=1424x1664\n"
           "09-19 18:20:01.1 I XRWiredXR: XRPIX 0.250 0.250 10 20 30\n"
           "09-19 18:20:01.1 I XRWiredXR: XRPIX 0.750 0.750 40 50 60\n")
    samples = vp.parse_probe(log)
    assert samples == [(0.25, 0.25, (10, 20, 30)), (0.75, 0.75, (40, 50, 60))]


def test_colour_report_measures_gamma_error_and_patch_accuracy():
    # A pipeline that applies sRGB encoding one extra time: every value comes back too bright.
    def double_encoded(value):
        linear = (value / 255.0) ** 2.2
        return round(255 * (linear ** (1 / 2.2) if False else (value / 255.0) ** (1 / 2.2)))

    perfect = [(u / 8 + 0.01, vp.PATCH_ROW, vp.expected_rgb(u / 8 + 0.01, vp.PATCH_ROW, 512, 256))
               for u in range(8)]
    report = vp.colour_report(perfect, 512, 256)
    assert report["max_patch_error"] <= 2 and 0.95 <= report["gamma"] <= 1.05

    broken = [(u, v, tuple(double_encoded(c) for c in rgb)) for u, v, rgb in perfect]
    bad = vp.colour_report(broken, 512, 256)
    assert bad["max_patch_error"] > 20 and bad["gamma"] < 0.8


def test_banding_report_counts_surviving_levels_in_the_ramp():
    smooth = [(x / 256, vp.GRADIENT_ROW, (x, x, x)) for x in range(256)]
    assert vp.banding_report(smooth, 512, 256)["levels"] == 256
    quantised = [(x / 256, vp.GRADIENT_ROW, ((x // 16) * 16,) * 3) for x in range(256)]
    report = vp.banding_report(quantised, 512, 256)
    assert report["levels"] == 16 and report["max_step"] == 16


def test_probe_parsing_keeps_only_the_latest_block():
    log = ("XRPIX grid=2 eye=8x8\nXRPIX 0.25 0.25 0 0 0\n"
           "XRPIX grid=2 eye=8x8\nXRPIX 0.25 0.25 9 9 9\n")
    assert vp.parse_probe(log) == [(0.25, 0.25, (9, 9, 9))]      # the run before video arrived is stale


def test_probe_header_reports_bit_depth():
    log = "XRPIX grid=256 eye=1856x2160 depth=10\nXRPIX 0.25 0.25 512 512 512\n"
    assert vp.probe_depth(log) == 10
    assert vp.probe_depth("XRPIX grid=256 eye=1856x2160\nXRPIX 0.25 0.25 5 5 5\n") == 8


def test_linear_swapchain_expectations_use_light_not_code_values():
    # A 10-bit linear swapchain stores light: sRGB 128 is 21.6% of full light, so 221 of 1023.
    assert vp.expected_stored((128, 128, 128), depth=10, transfer="linear") == (221, 221, 221)
    assert vp.expected_stored((128, 128, 128), depth=8, transfer="srgb") == (128, 128, 128)


def test_colour_report_handles_a_linear_ten_bit_probe():
    samples = []
    for patch in vp.PATCHES:
        u = (patch.slot + 0.5) / len(vp.PATCHES)
        samples.append((u, vp.PATCH_ROW, vp.expected_stored(patch.rgb, depth=10, transfer="linear")))
    report = vp.colour_report(samples, 512, 256, depth=10, transfer="linear")
    assert report["max_patch_error"] <= 2       # measured in the swapchain's own units


def test_black_crush_report_flags_crushed_shadows():
    # Clean ramp: output == input. Dark detail fully preserved.
    clean = [(x / 256, vp.GRADIENT_ROW, (x, x, x)) for x in range(256)]
    r = vp.black_crush_report(clean, 512, 256)
    assert r["crush_floor"] <= 1 and r["dark_levels"] >= 28

    # Crushed: everything below code 16 clamps to black.
    crushed = [(x / 256, vp.GRADIENT_ROW, (max(0, x - 16),) * 3) for x in range(256)]
    c = vp.black_crush_report(crushed, 512, 256)
    assert c["crush_floor"] >= 14 and c["dark_levels"] < r["dark_levels"]


def test_black_crush_report_scales_ten_bit_to_8bit():
    # 10-bit clean ramp (values 0..1023) should read as uncrushed on the 8-bit scale.
    clean10 = [(x / 256, vp.GRADIENT_ROW, (x * 4, x * 4, x * 4)) for x in range(256)]
    r = vp.black_crush_report(clean10, 512, 256, depth=10)
    assert r["crush_floor"] <= 1


def test_calibration_report_bundles_the_metrics():
    samples = []
    for patch in vp.PATCHES:
        u = (patch.slot + 0.5) / len(vp.PATCHES)
        samples.append((u, vp.PATCH_ROW, patch.rgb))
    samples += [(x / 256, vp.GRADIENT_ROW, (x, x, x)) for x in range(256)]
    rep = vp.calibration_report(samples, 512, 256)
    assert set(rep) >= {"gamma", "max_patch_error", "banding_levels", "crush_floor", "dark_levels"}
