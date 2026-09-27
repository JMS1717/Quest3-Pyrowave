import numpy as np

from xrbench import rdcorpus


def _clip(path, w=8, h=4, n=3):
    header = f"YUV4MPEG2 W{w} H{h} F90:1 Ip A1:1 XCOLORRANGE=FULL C444\n".encode()
    with open(path, "wb") as f:
        f.write(header)
        for i in range(n):
            f.write(b"FRAME\n")
            f.write(bytes([i * 10] * (3 * w * h)))


def test_clip_frames_and_extract_frame_round_trip(tmp_path):
    clip = tmp_path / "c.y4m"
    _clip(clip)
    assert rdcorpus.clip_frames(clip) == 3
    one = rdcorpus.extract_frame(clip, 2, tmp_path / "f.y4m")
    data = one.read_bytes()
    assert data.startswith(b"YUV4MPEG2 W8 H4") and data.count(b"FRAME\n") == 1
    assert data[data.index(b"FRAME\n") + 6:] == bytes([20] * 96)


def test_summarise_and_class_deltas_keep_classes_apart():
    rows = []
    for klass, base in (("panel", 50.0), ("home", 30.0)):
        for fi in (0, 10, 20):
            for wv, off in (("97", 0.0), ("haar", -2.0 if klass == "home" else +0.5)):
                rows.append({"class": klass, "clip": "clipA", "frame": fi, "wavelet": wv, "cap_bytes": 416667,
                             "actual_bytes": 416600, "psnr_y": base + off + fi * 0.01, "psnr_hvs": base + 3 + off, "ssim": 0.9, "vmaf": 80.0})
    s = rdcorpus.summarise(rows)
    assert len(s) == 4 and all(x["frames"] == 3 for x in s)
    home97 = next(x for x in s if x["class"] == "home" and x["wavelet"] == "97")
    assert abs(home97["psnr_y_mean"] - 30.1) < 1e-9 and home97["psnr_y_std"] > 0
    d = rdcorpus.class_deltas(s)
    assert abs(d["home@416667"]["d_psnr_y"] - (-2.0)) < 1e-9
    assert abs(d["panel@416667"]["d_psnr_y"] - 0.5) < 1e-9


def test_mbps_at_90hz():
    assert abs(rdcorpus.mbps(416667, 90) - 300.0) < 0.01


def test_temporal_class_bands_are_frozen():
    assert rdcorpus.temporal_class(0.0, 1.0) == "STATIC"
    assert rdcorpus.temporal_class(0.1, 0.0) == "STATIC"
    assert rdcorpus.temporal_class(0.5, 0.0) == "LOW_MOTION"
    assert rdcorpus.temporal_class(2.0, 0.0) == "ACTIVE"
    assert rdcorpus.temporal_class(6.0, 0.0) == "HIGH_MOTION"


def test_temporal_stats_and_class_rules(tmp_path):
    w, h = 16, 8
    header = f"YUV4MPEG2 W{w} H{h} F90:1 Ip A1:1 XCOLORRANGE=FULL C444\n".encode()
    clip = tmp_path / "c.y4m"
    with open(clip, "wb") as f:
        f.write(header)
        for i in range(12):
            y = np.full((h, w), (i * 3) % 256, np.uint8)     # every frame shifts luma by 3
            f.write(b"FRAME\n" + y.tobytes() + bytes(2 * w * h))
    s = rdcorpus.temporal_stats(clip, stride=10)
    assert s["frames"] == 12 and s["identical_consecutive_frac"] == 0.0 and abs(s["mean_consecutive_diff"] - 3.0) < 1e-6
    assert s["temporal_class"] == "ACTIVE" and abs(s["mean_stride_diff"] - 30.0) < 1e-6
    assert rdcorpus.clip_accepted("B_rotation", "LOW_MOTION") is False
    assert rdcorpus.clip_accepted("G_hud_text", "STATIC") is True
    assert rdcorpus.clip_accepted("A_gameplay", "STATIC") is False and rdcorpus.clip_accepted("A_gameplay", "LOW_MOTION") is True


def test_gate_bands_and_distribution():
    assert [rdcorpus.gate_for(v) for v in (0, 15, 15.1, 25, 25.1, 50, 50.1, None, float("nan"))] == \
        ["STRONG PASS", "STRONG PASS", "PASS", "PASS", "CONDITIONAL", "CONDITIONAL", "FAIL", "UNKNOWN", "UNKNOWN"]
    rows = [{"extra_pct": v, "frame": i * 10, "bound": False} for i, v in enumerate([10, 12, 11, 60, 13, 12, 14, 11, 12])]
    d = rdcorpus.overhead_distribution(rows)
    assert d["n"] == 9 and d["max"] == 60 and d["max_frame"] == 30 and d["median"] == 12 and d["p95"] is not None
    rows.append({"extra_pct": None, "frame": 90, "bound": True})
    d = rdcorpus.overhead_distribution(rows)
    assert d["n_bound"] == 1 and d["max_is_bound"] and d["max_frame"] == 90


def test_source_descriptors_and_roi_and_spearman():
    flat = np.full((32, 64), 100, np.uint8)
    d = rdcorpus.source_descriptors(flat)
    assert d["luma_entropy_bits"] == 0.0 and d["gradient_energy"] == 0.0 and d["flat_frac"] == 1.0
    rng = np.random.default_rng(1)
    noisy = rng.integers(0, 256, (32, 64), np.uint8)
    dn = rdcorpus.source_descriptors(noisy, prev_luma=flat)
    assert dn["luma_entropy_bits"] > 7 and dn["edge_density"] > 0.3 and dn["temporal_diff"] > 0 and dn["hf_energy_frac"] > 0.5
    dec = noisy.copy(); dec[0:8, 0:8] = 0
    r = rdcorpus.roi_psnr(noisy, dec, (0, 0, 8, 8))
    assert r["inside"] < 30 and r["outside"] == float("inf")
    assert abs(rdcorpus.spearman([1, 2, 3, 4, 5, 6], [2, 4, 6, 8, 10, 12]) - 1.0) < 1e-9
    assert rdcorpus.spearman([1, 2, 3], [1, 2, 3]) is None
