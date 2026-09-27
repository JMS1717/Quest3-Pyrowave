"""configure_alvr_2013.ps1 keeps its ALVR session backups beside the ALVR install it edits (the
folder above -Root), which is where the harness restores them from (runtime root \\backups). It used
to keep them beside the script, which, run from the repo, filled tools\\backups
while the harness read the runtime root's copies."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
PWSH = shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(PWSH is None, reason="needs PowerShell")
def test_restore_reads_the_backups_beside_the_alvr_install(tmp_path):
    install = tmp_path / "ALVR-20.13.0"
    install.mkdir()
    (install / "session.json").write_text(json.dumps({"which": "live"}))
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "alvr2013-session-20260101-000000.json").write_text(json.dumps({"which": "runtime-root backup"}))
    result = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             str(TOOLS / "configure_alvr_2013.ps1"), "-Root", str(install), "-Restore"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads((install / "session.json").read_text()) == {"which": "runtime-root backup"}
