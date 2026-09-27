"""The README's toggle tables list exactly the controls the beta dashboard shows
(tools/beta/toggles.tsv), each under the right heading, so the docs cannot drift from the UI."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def toggles():
    rows = {}
    for line in (REPO / "tools" / "beta" / "toggles.tsv").read_text().splitlines():
        if line and not line.startswith("#"):
            group, control = line.split("\t")
            rows.setdefault(group, []).append(control)
    return rows


def table_controls(section):
    text = (REPO / "README.md").read_text(encoding="utf-8")
    body = text.split(section, 1)[1].split("\n#", 1)[0]
    return [line.split("|")[1].strip() for line in body.splitlines()
            if line.startswith("| ") and not line.startswith("| Control") and "---" not in line]


def test_experiment_controls_match_the_dashboard():
    assert table_controls("### Experiment controls") == toggles()["experiment"]


def test_advanced_controls_match_the_dashboard():
    assert table_controls("### Advanced / Research controls") == toggles()["advanced"]


def test_the_recommended_profile_is_named_as_in_the_dashboard():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "PyroWave 4:4:4 (Recommended)" in text
    assert "Full-chroma experimental wireless VR streaming" in text
