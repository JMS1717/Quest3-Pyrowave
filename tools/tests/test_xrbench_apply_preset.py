import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import apply_preset as ap
from xrbench import plan as pl


def test_every_quality_preset_is_reachable_by_label():
    available = ap.presets()
    assert set(available) == {s.label for s in pl.quality_plan()}
    assert "Q-SCREEN-BEST" in available


def test_describe_reports_what_the_preset_buys_against_the_panel():
    line = ap.describe(ap.presets()["Q-SCREEN-BEST"])
    assert "Q-SCREEN-BEST" in line and "72 Hz" in line and "3520x3840/eye" in line
    # the two numbers that make presets comparable to the display rather than to each other
    assert "37.3 px/deg" in line
    assert "99% of panel" in line


def test_describe_flags_a_gaze_off_preset_loudly():
    assert "gaze OFF" in ap.describe(ap.presets()["Q-SCREEN-BEST-NG"])
    assert "gaze on" in ap.describe(ap.presets()["Q-SCREEN-BEST"])


def test_apply_passes_the_segments_own_configure_args(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(ap.subprocess, "run", fake_run)
    segment = ap.presets()["Q-SCREEN-BEST"]
    ap.apply(segment, "5050.client", "192.0.2.10")
    # on Windows apply() also sets the gaze variable afterwards; the configure call is the one
    # that runs the script
    configure = [c for c in calls if "-File" in c]
    assert len(configure) == 1, calls
    seen = {"cmd": configure[0]}
    for flag in ("-FoveationCenterX", "-FoveationCenterY", "-EyeHeight", "-RefreshHz"):
        assert flag in seen["cmd"], flag
    assert seen["cmd"][seen["cmd"].index("-EyeHeight") + 1] == "3840"
    assert seen["cmd"][seen["cmd"].index("-RefreshHz") + 1] == "72"


def test_apply_raises_rather_than_reporting_success_when_configure_fails(monkeypatch):
    monkeypatch.setattr(ap.subprocess, "run",
                        lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": "",
                                                         "stderr": "boom"})())
    with pytest.raises(RuntimeError, match="configure failed"):
        ap.apply(ap.presets()["Q-CONTROL"], "h", "ip")
