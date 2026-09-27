import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alvr_events_summary import load_events, summarize

FIXTURE = Path(__file__).parent / "fixtures" / "events_sample.json"


@pytest.fixture
def summary():
    return summarize(load_events(FIXTURE))


def test_decoder_mean_ms(summary):
    assert summary["decoder_ms"]["mean"] == pytest.approx(55.16785, abs=1e-3)


def test_decoder_p50_and_p95(summary):
    assert summary["decoder_ms"]["p50"] == pytest.approx((53.26219 + 56.64807) / 2, abs=1e-3)
    assert summary["decoder_ms"]["p95"] == pytest.approx(57.814322, abs=0.2)


def test_client_fps_mean(summary):
    assert summary["client_fps"]["mean"] == pytest.approx(63.00012, abs=1e-3)


def test_bitrate_mean_mbps(summary):
    assert summary["bitrate_mbps"]["mean"] == pytest.approx(318.4926, abs=1e-3)


def test_packet_loss_is_delta_of_summary_totals(summary):
    assert summary["packets_lost"] == 2


def test_frame_counts(summary):
    assert summary["graph_events"] == 4 and summary["summary_events"] == 2


def test_utf8_bom_is_accepted(tmp_path):
    bom_file = tmp_path / "bom.json"
    bom_file.write_bytes(b"\xef\xbb\xbf" + FIXTURE.read_bytes())
    assert len(load_events(bom_file)) == 6


def test_cli_table_and_csv(tmp_path):
    script = Path(__file__).resolve().parents[1] / "alvr_events_summary.py"
    csv_path = tmp_path / "out.csv"
    out = subprocess.run([sys.executable, str(script), str(FIXTURE), "--label", "wifi-375",
                          "--csv", str(csv_path)], capture_output=True, text=True, check=True).stdout
    assert "decoder_ms" in out and "55.17" in out
    rows = csv_path.read_text().splitlines()
    assert rows[0].startswith("label,") and rows[1].startswith("wifi-375,")


# --- per-frame rows (needs GraphStatistics.target_timestamp_ns / video_packet_bytes) ---

def graph_event(ts_ns=None, bytes_=None, **extra):
    data = {"decoder_s": 0.01, "client_fps": 72.0, "server_fps": 72.0,
            "bitrate_bps": 400e6, "throughput_bps": 500e6, **extra}
    if ts_ns is not None:
        data["target_timestamp_ns"] = ts_ns
    if bytes_ is not None:
        data["video_packet_bytes"] = bytes_
    return {"timestamp": "12:00:00.000", "event_type": {"id": "GraphStatistics", "data": data}}


def test_per_frame_rows_are_keyed_by_frame():
    from alvr_events_summary import per_frame_rows
    rows = per_frame_rows([graph_event(100, 50_000), graph_event(200, 60_000)])
    assert [r["target_timestamp_ns"] for r in rows] == [100, 200]
    assert [r["video_packet_bytes"] for r in rows] == [50_000, 60_000]


def test_per_frame_rows_carry_the_stage_latencies_in_ms():
    from alvr_events_summary import per_frame_rows
    row = per_frame_rows([graph_event(100, 50_000, decoder_s=0.0125)])[0]
    assert row["decoder_ms"] == pytest.approx(12.5)


def test_per_frame_rows_are_empty_without_a_frame_id():
    # an unpatched ALVR server emits GraphStatistics with no frame id; joining is then impossible
    # and silently returning unkeyed rows would invite exactly that mistake
    from alvr_events_summary import per_frame_rows
    assert per_frame_rows([graph_event(None, None)]) == []


def test_per_frame_rows_ignore_other_event_types():
    from alvr_events_summary import per_frame_rows
    other = {"event_type": {"id": "StatisticsSummary", "data": {"packets_lost_total": 1}}}
    assert len(per_frame_rows([graph_event(100, 1), other])) == 1


def test_the_existing_fixture_predates_the_frame_id():
    # guards the "empty means unpatched" contract against a real capture, not just a synthetic one
    from alvr_events_summary import per_frame_rows
    assert per_frame_rows(load_events(FIXTURE)) == []
