from xrbench import h264_corpus as h


def test_nvenc_args_match_the_raw_pipe_and_bitrate_is_measured():
    a = h.nvenc_args(300, fps=90, gop=90)
    assert a[:4] == ["-c:v", "h264_nvenc", "-preset", "p1"] and "-coder" in a and a[a.index("-coder") + 1] == "cavlc"
    assert a[a.index("-b:v") + 1] == "300M" and a[a.index("-bufsize") + 1] == "3333k" and a[a.index("-g") + 1] == "90" and "-bf" in a
    assert abs(h.achieved_mbps(90 * 416_667, 90, 90) - 300.0) < 0.01
