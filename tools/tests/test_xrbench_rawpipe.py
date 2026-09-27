import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import rawpipe as rp

AUD = b"\x00\x00\x00\x01\x09\xf0"
SPS = b"\x00\x00\x00\x01\x67\x64\x00\x33"
IDR = b"\x00\x00\x01\x65" + b"\x88" * 50
P = b"\x00\x00\x01\x41" + b"\x9a" * 30


def test_access_units_split_on_access_unit_delimiters():
    stream = AUD + SPS + IDR + AUD + P + AUD + P
    units = list(rp.access_units(io.BytesIO(stream), chunk=7))
    assert units == [AUD + SPS + IDR, AUD + P, AUD + P]


def test_frame_header_and_record_layout():
    header = rp.stream_header(3264, 1408, 72)
    assert header[:4] == b"XRW1" and len(header) == 16
    record = rp.frame_record(b"abc", pts_us=13888)
    assert record == (3).to_bytes(4, "big") + (13888).to_bytes(8, "big") + b"abc"


def test_parse_xrstat_lines():
    log = """09-19 15:40:01.1 I XRWired: something else
09-19 15:40:02.1 I XRWired: XRSTAT out_fps=71.9 in_mbps=398.2 dec_p50=6.10 dec_p95=9.80 dec_max=14.20 in_wait_ms=0.3 in_flight=1
09-19 15:40:03.1 I XRWired: XRSTAT out_fps=72.0 in_mbps=401.0 dec_p50=6.30 dec_p95=9.10 dec_max=12.00 in_wait_ms=0.2 in_flight=0"""
    rows = rp.parse_xrstat(log)
    assert len(rows) == 2 and rows[0]["out_fps"] == 71.9 and rows[1]["dec_p95"] == 9.1
    summary = rp.summarize_xrstat(rows)
    assert abs(summary["in_mbps"] - 399.6) < 0.01 and abs(summary["dec_p50_median"] - 6.2) < 1e-9


def test_ffmpeg_command_caps_bitrate_at_h264_level_limit():
    cmd = rp.ffmpeg_command(3264, 1408, 72, 1500)
    assert "1000M" in cmd and "h264_nvenc" in cmd
    assert rp.ffmpeg_command(3264, 1408, 72, 600)[cmd.index("-b:v") + 1] == "600M"


def test_clip_frames_loop_and_pace_at_stream_rate():
    frames = [b"a", b"b", b"c"]
    sched = list(rp.paced_schedule(frames, fps=72, count=7, t0=100.0))
    assert [f for _, f in sched] == [b"a", b"b", b"c", b"a", b"b", b"c", b"a"]
    assert abs(sched[1][0] - (100.0 + 1 / 72)) < 1e-9 and abs(sched[6][0] - (100.0 + 6 / 72)) < 1e-9


def test_flood_schedule_has_no_deadlines():
    sched = list(rp.paced_schedule([b"x"], fps=72, count=3, t0=5.0, flood=True))
    assert [t for t, _ in sched] == [None, None, None]


def test_clip_encode_command_is_offline_and_writes_file():
    cmd = rp.ffmpeg_command(3264, 1408, 72, 600, output="clip.h264", frames=432)
    assert "-re" not in cmd and cmd[-1] == "clip.h264" and "432" in cmd


def test_encoder_uses_cavlc_by_default_and_names_clips_by_coder():
    cmd = rp.ffmpeg_command(3264, 1408, 72, 400)
    assert cmd[cmd.index("-coder") + 1] == "cavlc"
    assert rp.ffmpeg_command(3264, 1408, 72, 400, coder="cabac")[cmd.index("-coder") + 1] == "cabac"
    assert "cavlc" in rp.clip_name(3264, 1408, 72, 400, "cavlc")


def test_ack_latency_summary_matches_send_and_ack_times():
    sent = {0: 10.000, 13888: 10.014, 27777: 10.028, 41666: 10.042}
    acks = {0: 10.020, 13888: 10.036, 27777: 10.058}          # last frame never acknowledged
    lat = rp.ack_latency(sent, acks, skip_first=0)
    assert lat["frames"] == 3 and lat["missing"] == 1
    assert abs(lat["p50_ms"] - 22.0) < 1e-6 and abs(lat["max_ms"] - 30.0) < 1e-6


def test_read_acks_parses_kind_and_pts_records():
    import struct
    payload = b"".join(struct.pack(">BQ", k, p) for k, p in ((1, 0), (1, 13888), (2, 0), (2, 13888)))
    got = []
    rp.read_acks(io.BytesIO(payload), lambda kind, pts, t, ahead_ns=0: got.append((kind, pts)))
    assert got == [(1, 0), (1, 13888), (2, 0), (2, 13888)]


def test_slices_option_reaches_encoder_and_clip_name():
    cmd = rp.ffmpeg_command(3264, 1408, 72, 800, slices=8)
    assert cmd[cmd.index("-slices") + 1] == "8"
    assert "-slices" not in rp.ffmpeg_command(3264, 1408, 72, 800)
    assert "s8" in rp.clip_name(3264, 1408, 72, 800, "cavlc", slices=8)


def test_udp_chunks_cover_the_frame_with_offsets():
    import struct
    au = bytes(range(256)) * 20                     # 5120 bytes
    grams = rp.udp_chunks(au, pts=41666, chunk=1400)
    assert len(grams) == 4 and all(len(g) <= 24 + 1400 for g in grams)
    rebuilt = bytearray(len(au))
    for g in grams:
        magic, pts, frame_len, offset, clen, flags = struct.unpack(">4sQIIHH", g[:24])
        assert magic == b"XRU1" and pts == 41666 and frame_len == len(au)
        rebuilt[offset:offset + clen] = g[24:24 + clen]
    assert bytes(rebuilt) == au


def test_udp_hello_and_ack_formats():
    import struct
    assert rp.udp_hello(3264, 1408, 72) == b"XRH1" + struct.pack(">III", 3264, 1408, 72)
    assert rp.parse_udp_ack(b"XRA1" + struct.pack(">BQ", 2, 13888)) == (2, 13888)
    assert rp.parse_udp_ack(b"junk") is None


def test_receiver_extras_from_run_modes():
    assert rp.receiver_extras([]) == ("", False)
    vendor, sliced = rp.receiver_extras(["fence", "early"])
    assert "vendor.qti-ext-output-sw-fence-enable.value=1" in vendor
    assert "vendor.qti-ext-dec-early-notify.value=1" in vendor and sliced is False
    vendor, sliced = rp.receiver_extras(["s4", "slicedel"])
    assert "vendor.qti-ext-dec-slice-delivery-mode.value=1" in vendor and sliced is True


def test_slice_variant_tokens():
    assert rp.slice_variant(["s4", "slicedel"]) == 0
    assert rp.slice_variant(["s4", "slicedel", "slicev2"]) == 2


def test_mvhevc_stream_header_carries_codec_id():
    header = rp.stream_header(2560, 1440, 72, codec="mvhevc")
    assert header == b"XRW2" + (2560).to_bytes(4, "big") + (1440).to_bytes(4, "big") + (72).to_bytes(4, "big") \
        + (1).to_bytes(4, "big")
    assert rp.stream_header(3264, 1408, 72, codec="avc")[:4] == b"XRW1"


def test_length_records_split_mvhevc_clip_file():
    clip = (3).to_bytes(4, "big") + b"abc" + (2).to_bytes(4, "big") + b"de"
    assert list(rp.length_records(io.BytesIO(clip))) == [b"abc", b"de"]


def test_mvhevc_clip_command_targets_half_the_total_rate_per_view():
    cmd = rp.mvhevc_clip_command(2560, 1440, 72, 200, frames=432, output="x.mvhevc", preset=1, ten_bit=True)
    assert cmd[cmd.index("--mbps") + 1] == "100"           # NVENC's MV-HEVC rate control is per view
    assert cmd[cmd.index("--frames") + 1] == "432" and cmd[cmd.index("--out") + 1] == "x.mvhevc"
    assert cmd[cmd.index("--preset") + 1] == "1" and "--10bit" in cmd
    assert "--10bit" not in rp.mvhevc_clip_command(2560, 1440, 72, 200, frames=432, output="x")


def test_mvhevc_clip_names_include_depth():
    assert rp.clip_name(2560, 1440, 72, 200, "mvhevc") == "mvhevc_2560x1440_72fps_200mbps.mvhevc"
    assert rp.clip_name(2560, 1440, 72, 200, "mvhevc10") == "mvhevc10_2560x1440_72fps_200mbps.mvhevc"


def test_run_spec_parsing_for_mvhevc_eye_size_and_preset():
    r = rp.parse_run_spec("wifi:300:mv10:e3840x2160:p4", "cavlc")
    assert (r["transport"], r["mbps"], r["coder"], r["preset"]) == ("wifi", 300, "mvhevc10", 4)
    assert (r["width"], r["height"]) == (3840, 2160)
    assert r["label"] == "raw-wifi-300-mvhevc10-e3840x2160-p4"
    mv = rp.parse_run_spec("adb:200:mv", "cavlc")
    assert (mv["coder"], mv["width"], mv["height"], mv["preset"]) == ("mvhevc", 2560, 1440, 1)


def test_run_spec_parsing_keeps_avc_labels():
    r = rp.parse_run_spec("wifi:800:s4:slicedel:slicev1", "cavlc")
    assert (r["coder"], r["slices"], r["width"], r["height"]) == ("cavlc", 4, 3264, 1408)
    assert r["modes"] == ["slicedel", "slicev1"] and not r["flood"]
    assert r["label"] == "raw-wifi-800-cavlc-s4-slicedel-slicev1"
    u = rp.parse_run_spec("udp:400:c60000:flood", "cavlc")
    assert u["udp_chunk"] == 60000 and u["label"] == "raw-udp-400-cavlc-c60000-flood"


def test_mvhevc_clip_exe_is_found_next_to_the_package_or_from_env(monkeypatch):
    default = Path(rp.mvhevc_clip_exe())
    assert default.parts[-2:] == ("mvhevc_clip", "mvhevc_clip.exe")
    assert default.parent.parent == Path(rp.__file__).resolve().parents[1]
    monkeypatch.setenv("XRBENCH_MVHEVC_CLIP", "D:/tools/enc.exe")
    assert rp.mvhevc_clip_command(64, 64, 72, 20, frames=1, output="o")[0] == "D:/tools/enc.exe"


def test_parse_health_reads_thermal_status_ap_temperature_and_battery():
    thermal = ("IsStatusOverride: false\nThermal Status: 2\nCached temperatures:\n"
               "\tTemperature{mValue=0.0, mType=2, mName=SUBBAT, mStatus=0}\n"
               "\tTemperature{mValue=69.0, mType=13, mName=AP, mStatus=2}\n")
    battery = "Current Battery Service state:\n  AC powered: true\n  level: 37\n  temperature: 330\n"
    assert rp.parse_health(thermal, battery) == {"thermal_status": 2, "ap_c": 69.0, "battery": 37}
    assert rp.parse_health("", "") == {"thermal_status": None, "ap_c": None, "battery": None}


def test_read_acks_handles_vr_photon_records():
    import struct
    stream = io.BytesIO(struct.pack(">BQ", 1, 100) + struct.pack(">BQq", 3, 200, 11_000_000)
                        + struct.pack(">BQ", 2, 300))
    seen = []
    rp.read_acks(stream, lambda kind, pts, t, ahead_ns=0: seen.append((kind, pts, ahead_ns)))
    assert seen == [(1, 100, 0), (3, 200, 11_000_000), (2, 300, 0)]


def test_photon_latency_adds_the_time_still_to_wait_for_the_display():
    sent = {10: 1.000, 20: 2.000}
    acks = {10: (1.020, 8_000_000), 20: (2.030, 6_000_000)}      # ack time, ns until the runtime shows it
    summary = rp.photon_latency(sent, acks, skip_first=0)
    assert round(summary["p50_ms"], 6) == 32.0 and summary["count"] == 2   # 20 ms + 8 ms, 30 ms + 6 ms


def test_stream_header_codec_ids_cover_depth_and_view_count():
    assert rp.CODEC_IDS == {"avc": 0, "mvhevc": 1, "mvhevc10": 2, "hevc": 3, "hevc10": 4}
    assert rp.stream_header(3264, 1408, 72, codec="hevc10")[:4] == b"XRW2"
    assert rp.stream_header(3264, 1408, 72, codec="hevc10")[-4:] == (4).to_bytes(4, "big")


def test_codec_for_coder_maps_encoder_names_to_stream_codecs():
    assert rp.codec_for_coder("cavlc") == "avc" and rp.codec_for_coder("cabac") == "avc"
    assert rp.codec_for_coder("mvhevc10") == "mvhevc10" and rp.codec_for_coder("hevc") == "hevc"


def test_nvenc_clip_command_selects_codec_and_depth_from_the_coder_name():
    hevc = rp.nvenc_clip_command(3264, 1408, 72, 200, frames=432, output="c.hevc", coder="hevc10")
    assert hevc[hevc.index("--codec") + 1] == "hevc" and "--10bit" in hevc
    assert hevc[hevc.index("--mbps") + 1] == "200"          # single view: the whole rate goes to it
    mv = rp.nvenc_clip_command(2560, 1440, 72, 200, frames=432, output="c.mvhevc", coder="mvhevc")
    assert mv[mv.index("--codec") + 1] == "mvhevc" and mv[mv.index("--mbps") + 1] == "100"
    assert "--10bit" not in mv


def test_receiver_package_switches_between_the_flat_and_vr_clients():
    assert rp.RECEIVER == "com.xrwired.receiver" and rp.RECEIVER_XR == "com.xrwired.receiverxr"
    assert rp.receiver_package(False) == rp.RECEIVER and rp.receiver_package(True) == rp.RECEIVER_XR


def test_vr_run_tokens_set_late_latch_margin_and_refresh_rate():
    r = rp.parse_run_spec("wifi:400:vr:m8:hz90:fence", "cavlc")
    assert r["vr"] and r["submit_margin"] == 8 and r["display_hz"] == 90
    assert r["label"] == "raw-vr-wifi-400-cavlc-m8-hz90-fence"
    plain = rp.parse_run_spec("wifi:400:vr", "cavlc")
    assert plain["submit_margin"] == 0 and plain["display_hz"] == 0


def test_receiver_extras_include_the_vr_knobs():
    assert rp.vr_extras(submit_margin=8, display_hz=90) == ["--ei", "submit_margin", "8",
                                                            "--ef", "display_hz", "90.0"]
    assert rp.vr_extras(submit_margin=0, display_hz=0) == []


def test_read_acks_skips_pose_records_from_the_vr_client():
    import struct
    pose = struct.pack(">BQ", 4, 7) + struct.pack(">21f", *([0.5] * 21))
    stream = io.BytesIO(struct.pack(">BQ", 1, 100) + pose + struct.pack(">BQq", 3, 200, 5_000_000))
    seen = []
    rp.read_acks(stream, lambda kind, pts, t, ahead_ns=0: seen.append((kind, pts, ahead_ns)))
    assert seen == [(1, 100, 0), (3, 200, 5_000_000)]      # the pose is for the driver, not the bench
