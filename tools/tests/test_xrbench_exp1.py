import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import exp1 as E

LOG = """09-23 10:00:00.000  1 1 I PYROWAVE-UDP: gpu decode ms: min 2.50 mean 3.70 p50 3.60 p95 4.50 p99 5.20 max 6.00
09-23 10:00:00.000  1 1 I PYROWAVE-UDP: convert ms: min 0.70 mean 0.90 p50 0.70 p95 2.00 p99 2.30 max 2.80
09-23 10:00:00.000  1 1 I PYROWAVE-UDP: submit->fence ms: min 5.00 mean 6.40 p50 6.20 p95 8.30 p99 8.90 max 9.40
09-23 10:00:10.000  1 1 I PYROWAVE-UDP: gpu decode ms: min 2.60 mean 3.90 p50 3.80 p95 4.70 p99 5.40 max 6.20
09-23 10:00:10.000  1 1 I PYROWAVE-UDP: frames complete 1400 partial 2 skipped 20 dropped 3 superseded 1 | stale pk 100 foreign 0 decode fail 0 | last decode 3.5
09-23 10:00:10.000  1 1 I pyroclient: decode path compute (forced)
"""


def test_report_lines_and_summary_parse():
    r = E.parse_report_lines(LOG)
    assert len(r["gpu decode"]) == 2 and r["gpu decode"][0]["p99"] == 5.2
    assert len(r["submit->fence"]) == 1
    s = E.parse_summary(LOG)
    assert s["complete"] == 1400 and s["dropped"] == 3
    assert E.parse_path(LOG) == ("compute", "forced")


def test_cell_stats_pool_reports_conservatively():
    c = E.cell_stats(E.parse_report_lines(LOG))
    assert c["gpu decode"]["mean"] == pytest.approx(3.8)     # mean of means
    assert c["gpu decode"]["p99"] == 5.4                     # max of p99
    assert c["gpu decode"]["min"] == 2.5 and c["gpu decode"]["reports"] == 2


def test_pair_deltas_and_k():
    frag = {"gpu decode": {"mean": 4.0, "p50": 3.9, "p95": 5.0, "p99": 5.5}, "submit->fence": {"mean": 8.0, "p50": 7.8, "p95": 10.0, "p99": 11.0}}
    comp = {"gpu decode": {"mean": 3.0, "p50": 2.9, "p95": 4.0, "p99": 4.5}, "submit->fence": {"mean": 7.5, "p50": 7.3, "p95": 9.6, "p99": 10.8}}
    d = E.pair_deltas(frag, comp)
    assert d["gpu decode"]["mean"] == pytest.approx(-25.0) and d["submit->fence"]["p99"] == pytest.approx(-1.818, abs=0.01)
    k = E.propagation_k(frag, comp, {"mean": 0.1, "p50": 0.1, "p95": 0.1, "p99": 2.0})
    assert k["mean"]["K"] == pytest.approx(0.5) and "K < 1" in k["mean"]["band"]
    assert k["p99"]["K"] is None, "GPU saving of 1.0 ms is below the 2.0 ms noise floor"
    assert E.k_band(1.0).startswith("K ~ 1") and E.k_band(-0.5).startswith("K < 0") and E.k_band(0.05).startswith("K ~ 0")


def test_bands_outcomes_and_thermal_match():
    assert E.interpretation_band(-25, -22).startswith("STRONG")
    assert E.interpretation_band(-10, -3).startswith("MODERATE")
    assert E.interpretation_band(-2, 1).startswith("NO MEANINGFUL")
    assert E.interpretation_band(8, 9).startswith("CONTRADICTION")
    assert E.outcome(-25, -22)[0] == "A" and E.outcome(-25, 0)[0] == "B" and E.outcome(0, 0)[0] == "C" and E.outcome(10, 2)[0] == "D"
    assert E.thermal_match(70.0, 72.5)[0] == "THERMALLY MATCHED"
    assert E.thermal_match(70.0, 74.0)[0] == "THERMALLY MISMATCHED"
    assert E.thermal_match(None, 70.0)[0] == "UNKNOWN"
