"""Objective image quality for live cells: pair the frames the headset was sent (bitstream tap)
with the lossless encoder input of the same frames (runtime dump), decode the tapped frames with
PyroWave's PC decoder and score them. Only pure parts are tested here; decoding needs the GPU."""
import struct
from pathlib import Path

import pytest

from xrbench import plan as pl
from xrbench import pyrowave_wave
from xrbench import quality
from xrbench import sweep


# --- tap reaches the PyroWave launcher --------------------------------------------------------

def test_pyro_env_carries_the_tap(tmp_path):
    f = tmp_path / "pyro_env.cmd"
    sweep.write_pyro_env(None, 0, 0, target=f, tap=r"C:\runs\PQ60-400\tap")
    assert "set ALVR_BITSTREAM_TAP=C:\\runs\\PQ60-400\\tap" in f.read_text()
    sweep.write_pyro_env(r"C:\c\x.y4m", 30, 100, target=f, tap=r"C:\t")
    text = f.read_text()
    assert "ALVR_PYROWAVE_DUMP=C:\\c\\x.y4m" in text and "ALVR_BITSTREAM_TAP=C:\\t" in text
    sweep.write_pyro_env(None, 0, 0, target=f, tap=None)
    assert not f.exists()


def test_set_tap_env_is_remembered_for_the_launcher(monkeypatch):
    monkeypatch.setattr(sweep.sys, "platform", "win32")
    monkeypatch.setattr(sweep.subprocess, "run", lambda *a, **k: None)
    sweep.set_tap_env(Path(r"C:\runs\x\tap"))
    assert sweep.segment_tap() == r"C:\runs\x\tap"
    sweep.set_tap_env(None)
    assert sweep.segment_tap() is None


# --- quality plan -------------------------------------------------------------------------------

def test_quality_plan_mirrors_the_matrix_with_a_dump_per_cell():
    matrix = {(s.eye, s.eye_h, s.mbps) for s in pl.pyro_matrix_plan()}
    q = pl.pyro_matrix_quality_plan()
    assert {(s.eye, s.eye_h, s.mbps) for s in q} == matrix
    assert all(s.label.startswith("PQ") and s.scene == "home" and s.quality_frames == pl.QUALITY_FRAMES for s in q)
    assert len(q) == 10


# --- pairing and files ----------------------------------------------------------------------------

def test_sidecar_and_matching(tmp_path):
    side = tmp_path / "source.y4m.frames.csv"
    side.write_text("dump_index,frame_count,target_timestamp_ns\n0,500,1000\n1,501,2000\n2,502,3000\n")
    frames = quality.read_sidecar(side)
    assert [f["ts"] for f in frames] == [1000, 2000, 3000]
    tap = [{"pts_ns": 2000, "offset": 0, "bytes": 5}, {"pts_ns": 3000, "offset": 5, "bytes": 7},
           {"pts_ns": 9000, "offset": 12, "bytes": 3}]
    pairs, missing = quality.match(frames, tap)
    assert [(p[0], p[1]["pts_ns"]) for p in pairs] == [(1, 2000), (2, 3000)]
    assert missing == [0]


def test_tap_dimensions_come_from_the_index_header(tmp_path):
    idx = tmp_path / "tap-1.idx"
    idx.write_text("# alvr-bitstream-tap v1 codec=h264 width=2304 height=1056\npts_ns,offset,bytes,idr\n")
    assert quality.tap_dimensions(idx) == (2304, 1056)


def test_wave_holds_exactly_the_matched_frames(tmp_path):
    payload = tmp_path / "p.bin"
    payload.write_bytes(b"AAAAABBBBBBBCCC")
    rows = [{"pts_ns": 2, "offset": 5, "bytes": 7}, {"pts_ns": 1, "offset": 0, "bytes": 5}]
    out = tmp_path / "x.wave"
    assert quality.write_wave(payload, rows, out, 64, 32) == 2
    data = out.read_bytes()
    head = pyrowave_wave.header(64, 32, fps_num=90)
    assert data.startswith(head)
    body = data[len(head):]
    assert body == struct.pack("<I", 7) + b"BBBBBBB" + struct.pack("<I", 5) + b"AAAAA"


def tiny_y4m(path, frames, w=4, h=2, full=True):
    head = f"YUV4MPEG2 W{w} H{h} F90:1 Ip A1:1{' XCOLORRANGE=FULL' if full else ''} C444\n".encode()
    body = b"".join(b"FRAME\n" + bytes([i]) * (w * h * 3) for i in range(frames))
    path.write_bytes(head + body)


def test_y4m_subset_keeps_the_header_and_the_chosen_frames(tmp_path):
    src, out = tmp_path / "s.y4m", tmp_path / "o.y4m"
    tiny_y4m(src, 4)
    assert quality.write_y4m_subset(src, [2, 0], out) == 2
    data = out.read_bytes()
    assert data.startswith(src.read_bytes().split(b"\n", 1)[0] + b"\n")
    frames = data.split(b"FRAME\n")[1:]
    assert [f[0] for f in frames] == [2, 0] and all(len(f) == 24 for f in frames)


def test_decoded_output_is_declared_full_range(tmp_path):
    y = tmp_path / "d.y4m"
    tiny_y4m(y, 2, full=False)
    quality.ensure_full_range(y)
    assert b"XCOLORRANGE=FULL" in y.read_bytes().split(b"\n", 1)[0]
    before = y.read_bytes()
    quality.ensure_full_range(y)              # idempotent
    assert y.read_bytes() == before


def test_switching_the_tap_off_disarms_the_launcher(monkeypatch, tmp_path):
    # Observed: the sweep's teardown cleared the dump (rewriting pyro_env.cmd with the tap still
    # set) and then the tap (without rewriting), leaving the tap armed for every later SteamVR start
    env = tmp_path / "pyro_env.cmd"
    monkeypatch.setattr(sweep, "PYRO_ENV_CMD", env)
    monkeypatch.setattr(sweep.sys, "platform", "win32")
    monkeypatch.setattr(sweep.subprocess, "run", lambda *a, **k: None)
    sweep.set_tap_env(tmp_path / "cell" / "tap")
    sweep.set_dump_env(None, 0, 0)
    assert "ALVR_BITSTREAM_TAP" in env.read_text()
    sweep.set_tap_env(None)                    # the teardown order: dump first, then tap
    assert not env.exists()


def test_the_tap_follows_a_dump_set_before_it(monkeypatch, tmp_path):
    env = tmp_path / "pyro_env.cmd"
    monkeypatch.setattr(sweep, "PYRO_ENV_CMD", env)
    monkeypatch.setattr(sweep.sys, "platform", "win32")
    monkeypatch.setattr(sweep.subprocess, "run", lambda *a, **k: None)
    sweep.set_dump_env(str(tmp_path / "c" / "x.y4m"), 30, 100)
    sweep.set_tap_env(tmp_path / "t")
    text = env.read_text()
    assert "ALVR_PYROWAVE_DUMP=" in text and "ALVR_BITSTREAM_TAP=" in text
    sweep.set_tap_env(None); sweep.set_dump_env(None, 0, 0)
    assert not env.exists()
