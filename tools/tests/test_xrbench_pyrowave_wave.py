import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import pyrowave_wave as pw

FRAMES = [b"first-frame-bytes", b"second", b"third-frame-is-longer"]


def _capture(tmp_path, frames=FRAMES):
    """A tap capture: the frames concatenated, plus an index locating each one."""
    payload = tmp_path / "pw.h264"
    payload.write_bytes(b"".join(frames))

    lines = ["# alvr-bitstream-tap v1 codec=h264", "pts_ns,offset,bytes,idr"]
    offset = 0
    for i, frame in enumerate(frames):
        lines.append(f"{1000 + i},{offset},{len(frame)},{1 if i == 0 else 0}")
        offset += len(frame)
    index = tmp_path / "pw.idx"
    index.write_text("\n".join(lines) + "\n")
    return payload, index


def _read_wave(path):
    """Parse a .wave back into (header_fields, [frame_bytes])."""
    blob = path.read_bytes()
    assert blob[:8] == pw.MAGIC
    fields = struct.unpack_from("<8i", blob, 8)
    frames = []
    pos = 40
    while pos < len(blob):
        (size,) = struct.unpack_from("<I", blob, pos)
        pos += 4
        frames.append(blob[pos : pos + size])
        pos += size
    return fields, frames


def test_header_is_magic_plus_eight_ints():
    head = pw.header(3328, 1472)
    assert head[:8] == pw.MAGIC
    assert len(head) == 40


def test_header_describes_444_full_range_by_default():
    width, height, fmt, chroma, full_range, fps_num, fps_den, _ = struct.unpack_from(
        "<8i", pw.header(3328, 1472), 8
    )
    assert (width, height) == (3328, 1472)
    assert fmt == pw.FORMAT_YUV444P
    assert chroma == pw.CHROMA_444
    assert full_range == 1
    assert (fps_num, fps_den) == (72, 1)


def test_header_420_selects_the_matching_format():
    # The format and chroma fields are read separately by the decoder, so they must agree.
    _, _, fmt, chroma, *_ = struct.unpack_from("<8i", pw.header(64, 64, chroma=pw.CHROMA_420), 8)
    assert fmt == pw.FORMAT_YUV420P
    assert chroma == pw.CHROMA_420


def test_header_can_say_limited_range():
    fields = struct.unpack_from("<8i", pw.header(64, 64, full_range=False), 8)
    assert fields[4] == 0


@pytest.mark.parametrize("width,height", [(0, 64), (64, 0), (-1, 64)])
def test_header_rejects_bad_dimensions(width, height):
    with pytest.raises(ValueError):
        pw.header(width, height)


def test_header_rejects_unknown_chroma():
    with pytest.raises(ValueError):
        pw.header(64, 64, chroma=7)


def test_convert_round_trips_every_frame(tmp_path):
    payload, index = _capture(tmp_path)
    out = tmp_path / "out.wave"

    assert pw.convert(payload, index, out, 3328, 1472) == len(FRAMES)

    fields, frames = _read_wave(out)
    assert fields[0:2] == (3328, 1472)
    assert frames == FRAMES


def test_convert_uses_index_offsets_not_scanning(tmp_path):
    # A PyroWave bitstream has no start codes, so frames can only be found by offset. Padding
    # between frames must therefore be skipped rather than swallowed into a frame.
    payload = tmp_path / "pw.h264"
    payload.write_bytes(b"AAA" + b"\x00\x00\x00\x00" + b"BBBB")
    index = tmp_path / "pw.idx"
    index.write_text("pts_ns,offset,bytes,idr\n1,0,3,1\n2,7,4,0\n")

    out = tmp_path / "out.wave"
    assert pw.convert(payload, index, out, 64, 64) == 2
    _, frames = _read_wave(out)
    assert frames == [b"AAA", b"BBBB"]


def test_convert_stops_on_a_truncated_capture(tmp_path):
    # A sweep that is killed mid-write leaves the last frame short. Emitting it would corrupt the
    # length prefix of everything after it, so the converter stops instead.
    payload, index = _capture(tmp_path)
    payload.write_bytes(payload.read_bytes()[:-5])

    out = tmp_path / "out.wave"
    assert pw.convert(payload, index, out, 64, 64) == len(FRAMES) - 1
    _, frames = _read_wave(out)
    assert frames == FRAMES[:-1]


def test_convert_rejects_an_empty_index(tmp_path):
    payload = tmp_path / "pw.h264"
    payload.write_bytes(b"")
    index = tmp_path / "pw.idx"
    index.write_text("pts_ns,offset,bytes,idr\n")

    with pytest.raises(ValueError):
        pw.convert(payload, index, tmp_path / "out.wave", 64, 64)


def test_cli_writes_a_wave(tmp_path, capsys):
    payload, index = _capture(tmp_path)
    out = tmp_path / "cli.wave"

    rc = pw.main([str(payload), str(index), str(out), "--width", "3328", "--height", "1472"])

    assert rc == 0
    assert "3 frame(s)" in capsys.readouterr().out
    _, frames = _read_wave(out)
    assert frames == FRAMES


def test_udp_packet_records_with_one_pts_merge_into_a_frame():
    from xrbench.pyrowave_wave import merge_packets
    rows = [{"pts_ns": 1, "offset": 0, "bytes": 1368, "idr": False},
            {"pts_ns": 1, "offset": 1368, "bytes": 1368, "idr": False},
            {"pts_ns": 1, "offset": 2736, "bytes": 500, "idr": False},
            {"pts_ns": 2, "offset": 3236, "bytes": 1368, "idr": False},
            {"pts_ns": 2, "offset": 4604, "bytes": 200, "idr": False}]
    merged = merge_packets(rows)
    assert [(r["pts_ns"], r["offset"], r["bytes"]) for r in merged] == [(1, 0, 3236), (2, 3236, 1568)]
    # a TCP tap (one record per frame) is unchanged
    assert merge_packets(rows[3:4]) == rows[3:4]
