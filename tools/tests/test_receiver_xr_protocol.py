"""Compiles and runs the XR receiver's pure-Java helpers with the JDK (the Android build has no JUnit)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "receiver-xr" / "app" / "src"
ANDROID_HOME = os.environ.get("ANDROID_HOME")
ANDROID_JAR = Path(ANDROID_HOME, "platforms", "android-35", "android.jar") if ANDROID_HOME else None


@pytest.mark.skipif(shutil.which("javac") is None, reason="needs a JDK")
def test_hevc_parameter_sets_and_keyframes(tmp_path):
    """Hevc.parameterSets() collects every layer's VPS/SPS/PPS and isKeyframe() spots IRAP pictures."""
    sources = [SRC / "main/java/com/xrwired/receiverxr/Hevc.java",
               SRC / "jvmtest/java/com/xrwired/receiverxr/HevcTest.java"]
    subprocess.run(["javac", "-d", str(tmp_path), *map(str, sources)], check=True, capture_output=True, text=True)
    run = subprocess.run(["java", "-ea", "-cp", str(tmp_path), "com.xrwired.receiverxr.HevcTest"],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "PASS" in run.stdout


@pytest.mark.skipif(shutil.which("javac") is None or ANDROID_JAR is None or not ANDROID_JAR.exists(),
                    reason="needs a JDK and android.jar (ANDROID_HOME)")
def test_receiver_sources_compile_against_android(tmp_path):
    """The networking/MediaCodec layer builds against the platform jar (the gradle app owns the rest)."""
    sources = sorted((SRC / "main/java/com/xrwired/receiverxr").glob("*.java"))
    # android.jar goes on the class path, not -bootclasspath: a JDK 17 javac rejects -bootclasspath at
    # source/target 17, and at 8 the platform jar has no LambdaMetafactory (D8 desugars that in gradle).
    build = subprocess.run(["javac", "-cp", str(ANDROID_JAR), "-d", str(tmp_path), *map(str, sources)],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    assert (tmp_path / "com/xrwired/receiverxr/StreamReceiver.class").exists()
