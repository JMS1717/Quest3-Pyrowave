"""configure_alvr_2013.ps1 writes PyroWave into the session the way the beta dashboard does:
video.preferred_codec and the video.pyrowave section, plus foveation's follow_gaze. -WhatIf lists
what it would send without touching an ALVR install."""
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
PWSH = shutil.which("powershell") or shutil.which("pwsh")


def whatif(*args):
    result = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             str(TOOLS / "configure_alvr_2013.ps1"), "-WhatIf", *args],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return {line.split(" = ", 1)[0]: line.split(" = ", 1)[1]
            for line in result.stdout.splitlines() if " = " in line}


@pytest.mark.skipif(PWSH is None, reason="needs PowerShell")
def test_pyrowave_goes_into_the_session():
    sent = whatif("-Codec", "PyroWave", "-PyroTransport", "Udp", "-Wavelet", "53",
                  "-DecodePath", "fragment", "-FollowGaze", "off")
    assert sent["video.preferred_codec.variant"] == "PyroWave"
    assert sent["video.pyrowave.transport.variant"] == "Udp"
    assert sent["video.pyrowave.wavelet.variant"] == "Cdf53"
    assert sent["video.pyrowave.decode_path.variant"] == "Fragment"
    assert sent["video.foveated_encoding.content.follow_gaze"] == "False"


@pytest.mark.skipif(PWSH is None, reason="needs PowerShell")
def test_defaults_are_the_recommended_profile_knobs():
    sent = whatif()
    assert sent["video.pyrowave.transport.variant"] == "Udp"
    assert sent["video.pyrowave.wavelet.variant"] == "Cdf97"
    assert sent["video.pyrowave.decode_path.variant"] == "Compute"
    assert sent["video.foveated_encoding.content.follow_gaze"] == "True"


def test_the_pyrowave_launcher_no_longer_forces_the_codec():
    # the session picks PyroWave now; the launcher keeps only the research dump/tap plumbing
    text = (TOOLS / "windows" / "start_steamvr_pyro_clean.cmd").read_text()
    assert "set ALVR_PYROWAVE=1" not in text
    assert "set ALVR_PYROWAVE_UDP=1" not in text
    assert "ALVR_PYROWAVE_DUMP_TRIGGER" in text


@pytest.mark.skipif(PWSH is None, reason="needs PowerShell")
def test_the_headset_is_added_and_trusted_not_only_updated():
    # a fresh (or reset) session has no client entry; SetManualIps alone would do nothing
    result = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             str(TOOLS / "configure_alvr_2013.ps1"), "-WhatIf",
                             "-ClientHostname", "1234.client", "-ClientWifiIp", "192.0.2.10"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "client 1234.client: AddIfMissing (trusted), SetManualIps 192.0.2.10, Trust" in result.stdout
