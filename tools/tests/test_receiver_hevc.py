"""Runs the receiver's pure-Java HEVC helper test with the JDK (the Android build has no JUnit)."""
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "receiver" / "app" / "src"


@pytest.mark.skipif(shutil.which("javac") is None, reason="needs a JDK")
def test_hevc_parameter_set_extraction(tmp_path):
    sources = [SRC / "main/java/com/xrwired/receiver/Hevc.java", SRC / "jvmtest/java/com/xrwired/receiver/HevcTest.java"]
    subprocess.run(["javac", "-d", str(tmp_path), *map(str, sources)], check=True, capture_output=True, text=True)
    run = subprocess.run(["java", "-ea", "-cp", str(tmp_path), "com.xrwired.receiver.HevcTest"],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "PASS" in run.stdout
