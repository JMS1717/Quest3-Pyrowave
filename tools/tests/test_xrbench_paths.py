"""The harness separates its code (the repo's tools/) from its runtime root (the ALVR install, adb,
backups, pyro_env.cmd, corpus, runs). Before the workspace move they were one folder, <workspace>
on C:, and three runtime paths were hard-coded to it, so a harness run from anywhere else
still wrote pyro_env.cmd and read the corpus on C:."""
import importlib
from pathlib import Path

import pytest

from xrbench import paths


def test_explicit_root_wins(tmp_path):
    assert paths.runtime_root(tmp_path / "code", {"XRBENCH_ROOT": str(tmp_path / "rt")}) == tmp_path / "rt"


def test_workspace_bench_is_the_default_when_present(tmp_path):
    code = tmp_path / "ws" / "ALVR_Custom_Galaxy_XR" / "tools"
    code.mkdir(parents=True)
    (tmp_path / "ws" / "bench").mkdir()
    assert paths.runtime_root(code, {}) == tmp_path / "ws" / "bench"


def test_flat_layout_uses_the_code_folder(tmp_path):
    code = tmp_path / "XR_Wired"
    code.mkdir(parents=True)
    assert paths.runtime_root(code, {}) == code


@pytest.fixture
def rooted(monkeypatch, tmp_path):
    """Reload the harness modules under XRBENCH_ROOT=tmp_path; restore them afterwards."""
    from xrbench import plan, sweep, udptest
    monkeypatch.setenv("XRBENCH_ROOT", str(tmp_path))
    mods = [importlib.reload(m) for m in (paths, plan, sweep, udptest)]
    yield tmp_path, mods
    monkeypatch.delenv("XRBENCH_ROOT")
    for m in (paths, plan, sweep, udptest):
        importlib.reload(m)


def test_runtime_files_follow_the_root(rooted):
    root, (_, plan, sweep, udptest) = rooted
    assert sweep.PYRO_ENV_CMD == root / "pyro_env.cmd"
    assert Path(plan.CORPUS_ROOT) == root / "corpus"
    assert Path(plan.AB_PHOTO) == root / "xrbench" / "ab_photo.png"
    assert sweep.ALVR_ROOT == root / "ALVR-20.13.0"
    assert udptest.ADB == root / "android-tools" / "platform-tools" / "adb.exe"


def test_code_files_come_from_the_repo_whatever_the_root(rooted):
    root, (_, plan, sweep, udptest) = rooted
    tools = Path(__file__).resolve().parents[1]
    assert sweep.CODE == tools
    assert (sweep.CODE / "configure_alvr_2013.ps1").is_file()
    assert udptest.RECEIVER_LOCAL == tools / "udptest" / "udprecv-android"


def test_configure_targets_the_runtime_alvr_install(rooted, monkeypatch):
    # configure_alvr_2013.ps1 defaults -Root to its own folder; run from the repo that is
    # tools\ALVR-20.13.0, which does not exist (every cell failed until this).
    root, (_, plan, sweep, udptest) = rooted
    calls = []
    monkeypatch.setattr(sweep, "powershell", lambda *a, **k: calls.append(a) or "ok")
    seg = plan.pyro_path_plan()[1]
    sweep.configure_alvr(seg, "5050.client", "192.0.2.10")
    args = [str(a) for a in calls[0]]
    assert args[0] == str(sweep.CODE / "configure_alvr_2013.ps1")
    assert Path(args[args.index("-Root") + 1]) == root / "ALVR-20.13.0"
    assert args[args.index("-ClientWifiIp") + 1] == "192.0.2.10"


def test_apply_preset_targets_the_runtime_alvr_install(rooted, monkeypatch):
    root, _ = rooted
    from xrbench import apply_preset as ap
    importlib.reload(ap)
    calls = []
    monkeypatch.setattr(ap.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    ap.apply(ap.presets()["Q-CONTROL"], "h", "ip")
    cmd = next(c for c in calls if "-File" in c)
    assert Path(cmd[cmd.index("-Root") + 1]) == root / "ALVR-20.13.0"
