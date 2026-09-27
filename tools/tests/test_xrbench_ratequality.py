import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import ratequality as rq


def test_bytes_per_frame_matches_the_encoder_cap():
    # 600 Mbps at 72 fps is the cap the live encoder logged: 1,041,666.
    assert rq.bytes_per_frame(600, 72) == 1_041_666
    assert rq.bytes_per_frame(25, 72) == 43_402


@pytest.mark.parametrize("mbps,fps", [(0, 72), (-1, 72), (100, 0)])
def test_bytes_per_frame_rejects_nonsense(mbps, fps):
    with pytest.raises(ValueError):
        rq.bytes_per_frame(mbps, fps)


def test_parse_psnr_reads_ffmpeg_output():
    line = ("n:1 mse_avg:0.14 mse_y:0.11 mse_u:0.15 mse_v:0.17 "
            "psnr_avg:56.55 psnr_y:57.76 psnr_u:56.29 psnr_v:55.82")
    assert rq.parse_psnr(line) == {"psnr_y": 57.76, "psnr_u": 56.29, "psnr_v": 55.82}


def test_parse_psnr_handles_lossless():
    line = "n:1 mse_avg:0.00 psnr_avg:inf psnr_y:inf psnr_u:inf psnr_v:inf"
    assert rq.parse_psnr(line)["psnr_y"] == float("inf")


def test_parse_psnr_returns_none_when_absent():
    # A failed encode must read as missing, not as a real measurement of zero.
    assert rq.parse_psnr("ffmpeg: no such file") is None


@pytest.mark.parametrize("header,expected", [
    ("YUV4MPEG2 W8 H8 F72:1 Ip A1:1 XCOLORRANGE=FULL C444\n", "FULL"),
    ("YUV4MPEG2 W8 H8 F72:1 Ip A1:1 XCOLORRANGE=LIMITED C444\n", "LIMITED"),
    ("YUV4MPEG2 W8 H8 F72:1 Ip A1:1 C444\n", None),
])
def test_declared_colour_range(tmp_path, header, expected):
    path = tmp_path / "src.y4m"
    path.write_bytes(header.encode() + b"FRAME\n" + b"\x00" * 192)
    assert rq.declared_colour_range(path) == expected


def test_sweep_refuses_a_source_with_no_declared_range(tmp_path):
    # The guard that matters: ffmpeg silently converts between mismatched ranges, worth ~29 dB of
    # luma, and it looks exactly like a codec fault. Refuse rather than produce a plausible lie.
    path = tmp_path / "src.y4m"
    path.write_bytes(b"YUV4MPEG2 W8 H8 F72:1 Ip A1:1 C444\n" + b"FRAME\n" + b"\x00" * 192)
    with pytest.raises(ValueError, match="XCOLORRANGE"):
        rq.sweep(path)


def test_format_table_marks_missing_scores():
    rows = [{"mbps": 25, "cap_bytes": 43402, "actual_bytes": 43380,
             "psnr_y": 39.7, "psnr_u": 45.2, "psnr_v": 46.8},
            {"mbps": 50, "cap_bytes": 86805, "actual_bytes": 0,
             "psnr_y": None, "psnr_u": None, "psnr_v": None}]
    table = rq.format_table(rows)
    assert "39.70" in table
    assert "-" in table.splitlines()[2]
