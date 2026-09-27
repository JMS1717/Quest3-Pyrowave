"""send_video.py finds adb through the Android SDK the environment names, not one machine's path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import send_video


def test_adb_comes_from_android_home(tmp_path):
    assert send_video.adb_path({"ANDROID_HOME": str(tmp_path)}) == str(tmp_path / "platform-tools" / "adb")


def test_adb_falls_back_to_the_path_without_an_sdk():
    assert send_video.adb_path({}) == "adb"
