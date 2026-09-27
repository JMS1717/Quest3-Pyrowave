"""ALVR's crash_log.txt accumulates across days and stamps lines with the time of day only. The
harness used to keep any 'Error on parsing session config' line whose HH:MM:SS sorted after the
SteamVR start time, so an error left from a previous evening failed healthy cells run earlier in
the day (two cells, both quoting the same stale 22:01:14 line)."""
from xrbench import sweep

ERR = "[ERROR] Error on parsing session config (C:\\x\\session.json): syntax error at line 1 near:"


def test_only_lines_written_after_the_mark_count(tmp_path, monkeypatch):
    log = tmp_path / "crash_log.txt"
    log.write_text(f"22:01:14.756 {ERR}\n20:10:00.000 [INFO] something\n")
    monkeypatch.setattr(sweep, "ALVR_ROOT", tmp_path)
    mark = sweep.crash_log_mark()
    with open(log, "a") as f:
        f.write("20:41:20.000 [INFO] server started\n")
    assert sweep.alvr_parse_errors_since(mark) == []
    with open(log, "a") as f:
        f.write(f"20:41:21.000 {ERR}\n")
    errors = sweep.alvr_parse_errors_since(mark)
    assert len(errors) == 1 and errors[0].startswith("20:41:21")


def test_a_missing_or_replaced_log_is_handled(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "ALVR_ROOT", tmp_path)
    mark = sweep.crash_log_mark()                  # no log yet
    assert mark == 0
    (tmp_path / "crash_log.txt").write_text(f"20:41:21.000 {ERR}\n")
    assert len(sweep.alvr_parse_errors_since(mark)) == 1
    big = sweep.crash_log_mark()
    (tmp_path / "crash_log.txt").write_text("")    # rotated/truncated: start over from 0
    assert sweep.alvr_parse_errors_since(big) == []
