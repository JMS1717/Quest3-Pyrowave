from pathlib import Path

from xrbench import corpus_session as cs
from xrbench import rdcorpus


def test_trigger_line_and_clip_naming(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CORPUS", tmp_path)
    assert cs.trigger_line(Path(r"C:\c\A_gameplay\A_gameplay-01.y4m"), 90) == "C:\\c\\A_gameplay\\A_gameplay-01.y4m 90\n"
    (tmp_path / "A_gameplay").mkdir()
    (tmp_path / "A_gameplay" / "A_gameplay-01-x.y4m").write_bytes(b"")
    assert cs.next_clip_name("A_gameplay").startswith("A_gameplay-02-")
    assert cs.next_clip_name("E_foliage").startswith("E_foliage-01-")


def test_index_round_trip_status_and_freeze(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CORPUS", tmp_path)
    monkeypatch.setattr(cs, "INDEX", tmp_path / "index.csv")
    rows = [{"clip": "A_gameplay-01", "class": "A_gameplay", "title": "t", "frames": 90, "accepted": True, "temporal_class": "ACTIVE"},
            {"clip": "A_gameplay-02", "class": "A_gameplay", "title": "t", "frames": 90, "accepted": False, "temporal_class": "STATIC"},
            {"clip": "G_hud_text-01", "class": "G_hud_text", "title": "t", "frames": 90, "accepted": True, "temporal_class": "STATIC"}]
    cs.save_index(rows)
    back = cs.load_index()
    assert [r["clip"] for r in back] == ["A_gameplay-01", "A_gameplay-02", "G_hud_text-01"]
    lines = cs.status_lines(back)
    assert any(l.startswith("A_gameplay") and "1/4 accepted" in l and "short" in l for l in lines)
    assert any(l.startswith("G_hud_text") and "1/3" in l for l in lines)
    assert cs.set_roi("G_hud_text-01", [10, 20, 30, 40]).endswith("10 20 30 40")
    assert cs.load_index()[2]["roi"] == "10 20 30 40"
    frozen, digest = cs.freeze_index()
    assert frozen.exists() and len(digest) == 64 and frozen.with_suffix(".sha256").read_text().strip() == digest


def test_wait_for_clip_counts_sidecar_rows(tmp_path):
    p = tmp_path / "c.y4m"
    side = tmp_path / "c.y4m.frames.csv"
    side.write_text("dump_index,frame_count,target_timestamp_ns\n" + "".join(f"{i},{i},{i}\n" for i in range(5)))
    assert cs.wait_for_clip(p, 5, timeout=2) is True
    assert cs.wait_for_clip(p, 6, timeout=1) is False
