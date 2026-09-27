"""The ALVR build scripts point at clones that live outside this repo.

The clones live in a sibling directory of the repo, but the scripts once
kept resolving them relative to the repo root, so all three failed on their first line of real
work. These tests pin the resolution down, because a broken build script surfaces as a confusing
failure in whatever work prompted the rebuild rather than as itself.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
SCRIPTS = ["build_alvr_2013.sh", "build_alvr_stable_test.sh", "build_alvr_galaxy_xr.sh"]
# A POSIX sh: on Windows that is Git for Windows' sh (the `bash` on PATH there is WSL's).
SH = shutil.which("sh") or r"C:\Program Files\Git\bin\sh.exe"


def print_dir(script, env=None):
    result = subprocess.run([SH, str(TOOLS / script), "--print-dir"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"{script} --print-dir failed: {result.stderr}"
    return result.stdout.strip()


@pytest.mark.parametrize("script", SCRIPTS)
def test_print_dir_reports_a_path_outside_this_repo(script):
    # the clones are external inputs; resolving them inside the repo is the bug this pins
    resolved = Path(print_dir(script))
    assert resolved.is_absolute(), resolved
    assert TOOLS.parent not in resolved.parents, f"{script} resolves inside the repo: {resolved}"


@pytest.mark.parametrize("script", SCRIPTS)
def test_print_dir_names_the_expected_clone(script):
    expected = {"build_alvr_2013.sh": "ALVR-20.13.0",
                "build_alvr_stable_test.sh": "ALVR-stable-20.14.1",
                "build_alvr_galaxy_xr.sh": "ALVR"}[script]
    assert Path(print_dir(script)).name == expected


@pytest.mark.parametrize("script", SCRIPTS)
def test_inputs_root_is_overridable(script, tmp_path):
    import os
    env = dict(os.environ, XRWIRED_INPUTS=str(tmp_path))
    assert Path(print_dir(script, env=env)).parent == tmp_path / "research"


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_clone_actually_exists_on_this_machine(script):
    resolved = Path(print_dir(script))
    if not resolved.parent.exists():
        pytest.skip(f"inputs root {resolved.parent} not present on this machine")
    assert resolved.is_dir(), f"{script} points at a missing clone: {resolved}"


def test_workspace_layout_is_the_default_when_present():
    # the workspace holds the repo beside research\ (the clones) and toolchain\
    research = TOOLS.parents[1] / "research"
    if not research.is_dir():
        pytest.skip("not in the workspace layout")
    env = {k: v for k, v in os.environ.items() if k != "XRWIRED_INPUTS"}
    assert Path(print_dir("build_alvr_2013.sh", env=env)) == research / "ALVR-20.13.0"


def android_env(workspace, repo, env_overrides=None):
    """Source tools/lib/xrwired_env.sh as if the repo lived at `repo` and report what it resolved."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("XRWIRED_INPUTS", "XRWIRED_ANDROID_SDK", "XRWIRED_NDK", "XRWIRED_PYROWAVE",
                        "ANDROID_NDK_HOME", "JAVA_HOME")}
    env.update(env_overrides or {})
    script = ('workspace_dir="$1"; . "$2"; '
              'printf "%s\\n" "$inputs_dir" "$android_sdk" "$android_ndk" "$java_home" "$pyrowave_dir" "$ndk_bin"')
    result = subprocess.run([SH, "-c", script, "sh", str(repo), str(TOOLS / "lib" / "xrwired_env.sh")],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    keys = ["inputs", "sdk", "ndk", "java", "pyrowave", "ndk_bin"]
    return dict(zip(keys, result.stdout.splitlines()))


def make_workspace(root, host="windows-x86_64"):
    repo = root / "ALVR_Custom_Galaxy_XR"
    (repo / "tools").mkdir(parents=True)
    (root / "research" / "pyrowave").mkdir(parents=True)
    (root / "toolchain" / "android-sdk" / "ndk" / "27.2.12479018" / "toolchains" / "llvm" /
     "prebuilt" / host / "bin").mkdir(parents=True)
    (root / "toolchain" / "jdk-17" / "bin").mkdir(parents=True)
    return repo


def test_env_uses_the_workspace_toolchain_when_present(tmp_path):
    repo = make_workspace(tmp_path)
    got = android_env(tmp_path, repo)
    assert Path(got["inputs"]) == tmp_path
    assert Path(got["sdk"]) == tmp_path / "toolchain" / "android-sdk"
    assert Path(got["ndk"]) == tmp_path / "toolchain" / "android-sdk" / "ndk" / "27.2.12479018"
    assert Path(got["java"]) == tmp_path / "toolchain" / "jdk-17"
    assert Path(got["pyrowave"]) == tmp_path / "research" / "pyrowave"
    assert Path(got["ndk_bin"]).parts[-2:] == ("windows-x86_64", "bin")


def test_env_finds_the_host_prebuilt_whatever_it_is_called(tmp_path):
    repo = make_workspace(tmp_path, host="darwin-x86_64")
    assert Path(android_env(tmp_path, repo)["ndk_bin"]).parts[-2:] == ("darwin-x86_64", "bin")


def test_env_explicit_overrides_win(tmp_path):
    repo = make_workspace(tmp_path)
    sdk, jdk, pw = tmp_path / "sdk", tmp_path / "jdk", tmp_path / "pw"
    for d in (sdk, jdk, pw):
        d.mkdir()
    got = android_env(tmp_path, repo, {"XRWIRED_ANDROID_SDK": str(sdk), "JAVA_HOME": str(jdk),
                                       "XRWIRED_PYROWAVE": str(pw)})
    assert Path(got["sdk"]) == sdk
    assert Path(got["java"]) == jdk
    assert Path(got["pyrowave"]) == pw


def test_env_defaults_to_the_repo_parent_as_the_workspace(tmp_path):
    # no workspace folders yet (a fresh CI runner): the repo's parent is still the workspace, so
    # the clones and the toolchain resolve beside the repo, never inside it
    repo = tmp_path / "ALVR_Custom_Galaxy_XR"
    (repo / "tools").mkdir(parents=True)
    got = android_env(tmp_path, repo)
    assert Path(got["inputs"]) == tmp_path
    assert Path(os.path.normpath(got["sdk"])) == tmp_path / "toolchain" / "android-sdk"


def signing_env(repo, env_overrides=None):
    """What xrwired_env.sh hands cargo-apk for release signing: (keystore, password)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("XRWIRED_INPUTS", "CARGO_APK_RELEASE_KEYSTORE", "CARGO_APK_RELEASE_KEYSTORE_PASSWORD")}
    env.update(env_overrides or {})
    script = ('workspace_dir="$1"; . "$2"; '
              'printf "%s\\n%s\\n" "${CARGO_APK_RELEASE_KEYSTORE:-}" "${CARGO_APK_RELEASE_KEYSTORE_PASSWORD:-}"')
    result = subprocess.run([SH, "-c", script, "sh", str(repo), str(TOOLS / "lib" / "xrwired_env.sh")],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines() + ["", ""]
    return lines[0], lines[1]


def make_release_key(root):
    keys = root / "keys" / "beta-release"
    keys.mkdir(parents=True)
    (keys / "pyrowave-beta-release.p12").write_bytes(b"not a real keystore")
    (keys / "keystore-password.txt").write_text("s3cret")
    return keys


def test_local_builds_sign_with_the_workspace_release_key(tmp_path):
    # so an APK built on the PC installs over the CI-built beta without an uninstall
    repo = make_workspace(tmp_path)
    keys = make_release_key(tmp_path)
    keystore, password = signing_env(repo)
    assert Path(keystore) == keys / "pyrowave-beta-release.p12"
    assert password == "s3cret"


def test_without_a_release_key_the_debug_key_is_used(tmp_path):
    repo = make_workspace(tmp_path)
    assert signing_env(repo) == ("", "")


def test_an_explicit_keystore_wins(tmp_path):
    repo = make_workspace(tmp_path)
    make_release_key(tmp_path)
    got = signing_env(repo, {"CARGO_APK_RELEASE_KEYSTORE": "/ci/release.p12",
                             "CARGO_APK_RELEASE_KEYSTORE_PASSWORD": "from-ci"})
    assert got == ("/ci/release.p12", "from-ci")
