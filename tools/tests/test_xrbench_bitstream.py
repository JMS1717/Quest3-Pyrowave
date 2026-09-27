import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import bitstream as bs

SAMPLE = """# alvr-bitstream-tap v1 codec=h264
pts_ns,offset,bytes,idr
9215574029609,0,695439,1
9215587919609,695439,120040,0
9215601809609,815479,118220,0
"""


@pytest.fixture
def index(tmp_path):
    path = tmp_path / "stream.idx"
    path.write_text(SAMPLE)
    return path


def test_read_index_returns_typed_rows(index):
    rows = bs.read_index(index)
    assert len(rows) == 3
    assert rows[0] == {"pts_ns": 9215574029609, "offset": 0, "bytes": 695439, "idr": True}
    assert rows[1]["idr"] is False


def test_codec_is_read_from_the_header(index):
    assert bs.read_codec(index) == "h264"


def test_offsets_are_contiguous_so_the_payload_is_a_decodable_stream(index):
    # the .h264 beside the index is plain concatenated Annex B -- ffmpeg decodes it directly,
    # which is the whole point of the tap. A gap would mean a dropped frame went unrecorded.
    rows = bs.read_index(index)
    assert bs.gaps(rows) == []


def test_gaps_are_reported_when_offsets_do_not_line_up():
    rows = [{"pts_ns": 1, "offset": 0, "bytes": 100, "idr": True},
            {"pts_ns": 2, "offset": 150, "bytes": 100, "idr": False}]
    assert bs.gaps(rows) == [(0, 100, 150)]


def test_bitrate_uses_the_pts_span_not_the_frame_count(index):
    # frame count over a nominal rate would hide dropped frames; the timestamps will not
    rows = bs.read_index(index)
    span_s = (rows[-1]["pts_ns"] - rows[0]["pts_ns"]) / 1e9
    assert bs.bitrate_mbps(rows) == pytest.approx(sum(r["bytes"] for r in rows) * 8 / span_s / 1e6)


def test_bitrate_is_none_for_a_single_frame():
    assert bs.bitrate_mbps([{"pts_ns": 1, "offset": 0, "bytes": 10, "idr": True}]) is None


def test_idr_positions_are_reported_as_frame_indices(index):
    assert bs.idr_indices(bs.read_index(index)) == [0]


def test_empty_index_is_not_an_error(tmp_path):
    path = tmp_path / "empty.idx"
    path.write_text("# alvr-bitstream-tap v1 codec=h264\npts_ns,offset,bytes,idr\n")
    assert bs.read_index(path) == []
    assert bs.bitrate_mbps([]) is None


# --- frames encoded before a target timestamp exists ---

UNKEYED = """# alvr-bitstream-tap v1 codec=h264
pts_ns,offset,bytes,idr
0,0,49214,1
0,49214,49214,1
10075893371468,98428,700000,1
10075907261468,798428,700000,0
# frames=4 dropped=7
"""


@pytest.fixture
def unkeyed(tmp_path):
    path = tmp_path / "unkeyed.idx"
    path.write_text(UNKEYED)
    return path


def test_unkeyed_frames_are_still_read(unkeyed):
    # they belong in the stream -- they are IDRs the decoder needs -- so the index must list them
    assert len(bs.read_index(unkeyed)) == 4


def test_keyed_drops_frames_with_no_timestamp(unkeyed):
    rows = bs.keyed(bs.read_index(unkeyed))
    assert len(rows) == 2 and all(r["pts_ns"] > 0 for r in rows)


def test_bitrate_ignores_unkeyed_frames(unkeyed):
    # a pts of 0 would stretch the span to ~10000 s and report a few Mbps for a 400 Mbps stream
    rows = bs.read_index(unkeyed)
    assert bs.bitrate_mbps(rows) == pytest.approx(806.5, rel=0.01)


def test_offsets_stay_contiguous_across_unkeyed_frames(unkeyed):
    assert bs.gaps(bs.read_index(unkeyed)) == []


def test_trailing_counters_are_read(unkeyed):
    assert bs.read_counters(unkeyed) == {"frames": 4, "dropped": 7}


def test_counters_absent_when_the_capture_was_cut_short(index):
    # no trailing comment means Stop() never ran -- the capture may be truncated
    assert bs.read_counters(index) is None
