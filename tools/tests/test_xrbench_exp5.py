import json

from xrbench import exp5, rdcorpus


def test_class_overheads_and_live_gate():
    index = [{"clip": "A_gameplay-01", "class": "A_gameplay", "accepted": "True", "temporal_class": "ACTIVE"},
             {"clip": "A_gameplay-02", "class": "A_gameplay", "accepted": "True", "temporal_class": "ACTIVE"},
             {"clip": "E_foliage-01", "class": "E_foliage", "accepted": "True", "temporal_class": "ACTIVE"}]
    frames = []
    for clip, vals in (("A_gameplay-01", [10, 12, 14, 11, 13, 12, 12, 11, 15]), ("A_gameplay-02", [20, 22, 21, 24, 23, 22, 21, 22, 60]),
                       ("E_foliage-01", [55, 60, 70, 65, 58, 62, 61, 64, 66])):
        for i, v in enumerate(vals):
            frames.append({"clip": clip, "frame": i * 10, "extra_pct": v, "bound": False})
    cd = exp5.clip_overheads(frames)
    cls = exp5.class_overheads(index, cd)
    assert cls["A_gameplay"]["clips"] == 2 and cls["A_gameplay"]["gate"] == "PASS" and cls["A_gameplay"]["worst"] == 60
    assert cls["E_foliage"]["gate"] == "FAIL" and cls["B_rotation"]["gate"] == "UNKNOWN"
    warnings = exp5.failure_warnings(cls, cd, [])
    assert any("A_gameplay" in w and "> 50" in w for w in warnings) and any("E_foliage" in w for w in warnings)
    gate, why = exp5.live_gate(cls, warnings)
    assert gate == "UNKNOWN"
    assert exp5.live_gate(cls, [])[0] == "YES"
    assert exp5.live_gate({"A_gameplay": {"gate": "UNKNOWN"}}, [])[0] == "UNKNOWN"
    cls["A_gameplay"]["median_of_medians"] = 30.0
    assert exp5.live_gate(cls, [])[0] == "NO"


def test_render_empty_corpus_gives_all_unknown(tmp_path):
    txt = exp5.render(tmp_path, tmp_path / "R.md")
    heads = [l for l in txt.splitlines() if l.startswith("## ")]
    assert len(heads) == 21 and heads[0].startswith("## 1.") and heads[18].startswith("## 19.")
    assert "EXPLORATORY" in heads[19] and "Central question" in heads[20]
    assert txt.count("**UNKNOWN**") >= 8 and "Does the evidence justify" in txt
    assert json.loads((tmp_path / "summary.json").read_text())["live_gate"][0] == "UNKNOWN"
