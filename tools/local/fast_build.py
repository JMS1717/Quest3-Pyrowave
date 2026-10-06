"""Fast local Quest3-Pyrowave builds on a Windows development PC (see docs/LOCAL-BUILD.md).

CI starts every run from a fresh clone on 4-core runners, so it recompiles every ALVR crate and
takes about 20 minutes. This keeps one reconstruction under --root and refreshes it from this
repo's patches *by content*: unchanged files keep their timestamps, so Cargo, CMake and Ninja
rebuild only what a change touches. The build steps are the scripts CI runs.

  python tools/local/fast_build.py setup              # once: Android SDK/NDK, cargo tools, libclang
  python tools/local/fast_build.py build              # sync, client APK, Windows streamer, package
  python tools/local/fast_build.py build --client     # or --streamer
  python tools/local/fast_build.py test               # CI's Windows Rust tests and the Python suite

Local outputs are for iteration. They are signed with the stable key when it is available, so they
install over CI builds, but native libraries embed this PC's build paths and are not byte-identical
to CI's. Reviewed CI builds remain the evidence for publication and performance claims.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path(os.environ.get("Q3PW_FAST_ROOT", r"C:\q3pw\fast"))
TREES = ("ALVR-20.13.0", "pyrowave")
# Unpatched submodules present only in the full reconstruction; the sync never touches them.
UNMANAGED = ("ALVR-20.13.0/openvr", "pyrowave/Granite")
# Recipe files the build itself rewrites: cargo updates the workspace versions in Cargo.lock, and
# compile_foveation_shader.ps1 recompiles the FFE shader. Restoring the recipe copy before every
# build would only make Cargo and the C++ build redo work, so these are replaced only when the
# recipe's own copy changes.
BUILD_REWRITTEN = ("ALVR-20.13.0/Cargo.lock",
                   "ALVR-20.13.0/alvr/server_openvr/cpp/platform/win32/CompressAxisAlignedPixelShader.cso")
MANIFEST = ".q3pw-sync-manifest.json"

RUST = "1.97.1"
NDK = "27.2.12479018"
GRANITE_COMMIT = "842d9d5686ba8c799a7d34a78a68f98d6aeb5a68"
CMDLINE_TOOLS = ("https://dl.google.com/android/repository/commandlinetools-win-13114758_latest.zip",
                 "98b565cb657b012dae6794cefc0f66ae1efb4690c699b78a614b4a6a3505b003")
LIBCLANG = ("https://files.pythonhosted.org/packages/0b/2d/3f480b1e1d31eb3d6de5e3ef641954e5c67430d5ac93b7fa7e07589576c7/"
            "libclang-18.1.1-py2.py3-none-win_amd64.whl",
            "4dd2d3b82fab35e2bf9ca717d7b63ac990a3519c7e312f19fa8e86dcc712f7fb")
OPENXR_LOADER = ("https://github.com/KhronosGroup/OpenXR-SDK-Source/releases/download/release-1.0.34/"
                 "openxr_loader_for_android-1.0.34.aar",
                 "c8b50603ad3b81756c6bd203ab9c9d19341326e14f6cbae87ee894137ee5e256")
CARGO_TOOLS = (["cargo-ndk@4.1.2", "cbindgen@0.29.4"],
               ["--git", "https://github.com/zarik5/cargo-apk",
                "--rev", "0fd3126dad5aa1c5f0f26cdae3410f2e5af62c60", "cargo-apk"])
# Public certificate digest of the repository's stable signing key (CI's APK-CERTIFICATE.txt).
STABLE_CERT_SHA256 = "2f2c5b3bb99cd69edc58cc53fbac30325426e410b3fecafc76fe447549886779"
# The Windows test set of CI's streamer job, in its order.
WINDOWS_TESTS = [["alvr_adb", "--lib"], ["alvr_client_core", "--lib"], ["alvr_session", "--lib"],
                 ["alvr_packets", "--lib"], ["alvr_server_core", "--lib"],
                 ["alvr_server_io", "--lib", "initialization_tests"],
                 ["alvr_dashboard", "--bin", "alvr_dashboard"]]


# ---------------------------------------------------------------------------------------------
# Content sync: the part that makes rebuilds incremental


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def managed_files(stage):
    """Relative POSIX paths of every file the reconstruction recipe produces, minus git metadata."""
    out = []
    for tree in TREES:
        base = stage / tree
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = Path(dirpath).relative_to(stage).as_posix()
            dirnames[:] = [d for d in dirnames if d != ".git"
                           and f"{rel_dir}/{d}" not in UNMANAGED]
            out += [f"{rel_dir}/{name}" for name in filenames if name != ".git"]
    return sorted(out)


def _record(path, digest):
    st = path.stat()
    return {"sha256": digest, "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _current_digest(path, previous):
    """Digest of a tree file, trusting the manifest while size and mtime are unchanged."""
    st = path.stat()
    if previous and previous["size"] == st.st_size and previous["mtime_ns"] == st.st_mtime_ns:
        return previous["sha256"]
    return sha256(path)


def remove_tree(path):
    """shutil.rmtree that also removes git's read-only pack files on Windows."""
    def make_writable(func, name, _exc):
        os.chmod(name, 0o700)
        func(name)
    if Path(path).exists():
        shutil.rmtree(path, onexc=make_writable)


def _replace(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".q3pw-sync-tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def sync_tree(stage, tree, backup_dir, build_rewritten=BUILD_REWRITTEN):
    """Make the managed files of `tree` equal to `stage`, touching only files whose bytes differ.

    Files outside the recipe (build directories, target/, copied runtime libraries) are never
    examined. A managed file whose bytes differ from both the recipe and the last synced copy is a
    local edit: it is copied to `backup_dir` before it is overwritten or removed, so a hand edit in
    the build tree is never silently lost. `build_rewritten` files keep the build's version until
    the recipe's copy changes. Returns lists of changed, added, removed and backed-up paths.
    """
    stage, tree, backup_dir = Path(stage), Path(tree), Path(backup_dir)
    manifest_path = tree / MANIFEST
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    new, report = {}, {"changed": [], "added": [], "removed": [], "backed_up": []}

    def backup(rel, dst):
        target = backup_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, target)
        report["backed_up"].append(rel)

    for rel in managed_files(stage):
        src, dst = stage / rel, tree / rel
        digest = sha256(src)
        previous = old.get(rel)
        if dst.exists():
            if rel in build_rewritten and previous and previous.get("recipe") == digest:
                new[rel] = previous
                continue
            current = _current_digest(dst, previous)
            if current != digest:
                if rel not in build_rewritten and current != (previous or {}).get("sha256"):
                    backup(rel, dst)
                _replace(src, dst)
                report["changed"].append(rel)
        else:
            _replace(src, dst)
            report["added"].append(rel)
        new[rel] = dict(_record(dst, digest), recipe=digest)

    for rel in sorted(set(old) - set(new)):
        dst = tree / rel
        if dst.exists():
            if _current_digest(dst, old[rel]) != old[rel]["sha256"]:
                backup(rel, dst)
            dst.unlink()
            report["removed"].append(rel)

    tmp = manifest_path.with_name(MANIFEST + ".tmp")
    tmp.write_text(json.dumps(new, indent=0, sort_keys=True))
    os.replace(tmp, manifest_path)
    return report


# ---------------------------------------------------------------------------------------------
# Environment


def git_shell(name):
    """Git for Windows' sh/bash; the `bash` on a Windows PATH can be WSL's."""
    if os.name != "nt":
        return shutil.which(name)
    git = shutil.which("git")
    if git:
        candidate = Path(git).resolve().parents[1] / "bin" / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return rf"C:\Program Files\Git\bin\{name}.exe"


def find_jdk17():
    candidates = [os.environ.get("JAVA_HOME", "")]
    for parent in (r"C:\Program Files\Java", r"C:\Program Files\Eclipse Adoptium",
                   r"C:\Program Files\Microsoft"):
        if Path(parent).is_dir():
            candidates += [str(p) for p in sorted(Path(parent).glob("jdk-17*"))]
    for c in candidates:
        if c and (Path(c) / "bin" / "java.exe").exists() and "17" in Path(c).name:
            return c
    return os.environ.get("JAVA_HOME", "")


def vs_generator():
    """CMake generator for the newest Visual Studio that actually has the x64 C++ tools."""
    if os.environ.get("Q3PW_CMAKE_GENERATOR"):
        return os.environ["Q3PW_CMAKE_GENERATOR"]
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.exists():
        out = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                              "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                              "-property", "catalog_productLineVersion"],
                             capture_output=True, text=True).stdout.strip()
        if out == "2019":
            return "Visual Studio 16 2019"
    return "Visual Studio 17 2022"


def signing_key(args):
    if os.environ.get("CARGO_APK_RELEASE_KEYSTORE"):
        return os.environ["CARGO_APK_RELEASE_KEYSTORE"], os.environ.get("CARGO_APK_RELEASE_KEYSTORE_PASSWORD")
    keystore = Path(args.keystore) if args.keystore else \
        REPO.parent / "workspace" / "keys" / "quest3-release" / "quest3-release.p12"
    password = Path(args.keystore_password_file) if args.keystore_password_file else \
        keystore.with_name("keystore-password.txt")
    if keystore.exists() and password.exists():
        return str(keystore), password.read_text(encoding="utf-8").strip()
    return None, None


def build_env(root, args):
    tc = root / "toolchain"
    env = dict(os.environ)
    env.update({
        "XRWIRED_INPUTS": str(root),
        "XRWIRED_ANDROID_SDK": str(tc / "android-sdk"),
        "XRWIRED_NDK": str(tc / "android-sdk" / "ndk" / NDK),
        "JAVA_HOME": find_jdk17(),
        "LIBCLANG_PATH": str(tc / "libclang"),
        "RUSTUP_TOOLCHAIN": RUST,
        "Q3PW_CMAKE_GENERATOR": vs_generator(),
        "PATH": os.pathsep.join([str(tc / "shim"), str(tc / "cargo-tools" / "bin"), env["PATH"]]),
    })
    keystore, password = signing_key(args)
    if keystore:
        env["CARGO_APK_RELEASE_KEYSTORE"] = keystore
        env["CARGO_APK_RELEASE_KEYSTORE_PASSWORD"] = password
    return env, bool(keystore)


# ---------------------------------------------------------------------------------------------
# Steps


class Timer:
    def __init__(self):
        self.rows = []

    def run(self, label, fn):
        start = time.monotonic()
        print(f"== {label}", flush=True)
        result = fn()
        self.rows.append((label, time.monotonic() - start))
        print(f"   {label}: {self.rows[-1][1]:.1f} s", flush=True)
        return result

    def summary(self):
        width = max(len(r[0]) for r in self.rows)
        lines = [f"{label.ljust(width)}  {sec:7.1f} s" for label, sec in self.rows]
        lines.append(f"{'total'.ljust(width)}  {sum(r[1] for r in self.rows):7.1f} s")
        return "\n".join(lines)


# Compilers inherit this, so a build yields to interactive work instead of competing with it.
LOW_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
VR_PROCESSES = ("vrserver.exe", "vrcompositor.exe")


def running_vr():
    """SteamVR processes that are running; AGENTS.md forbids heavy builds while the owner plays."""
    if os.name != "nt":
        return []
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout.lower()
    return [name for name in VR_PROCESSES if f'"{name}"' in out]


def run(cmd, env, log, cwd=REPO):
    with open(log, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n$ {' '.join(map(str, cmd))}\n")
        f.flush()
        # A PowerShell 7 parent puts its own module directories first in PSModulePath; Windows
        # PowerShell 5.1 then cannot load Get-FileHash and the shader script fails. Without the
        # variable, 5.1 uses its own default module path.
        env = {k: v for k, v in env.items() if k.upper() != "PSMODULEPATH"}
        rc = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, stdout=f,
                            stderr=subprocess.STDOUT, creationflags=LOW_PRIORITY).returncode
    if rc:
        tail = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
        sys.exit(f"failed ({rc}): {' '.join(map(str, cmd))}\n" + "\n".join(tail) + f"\nfull log: {log}")


def download(url, digest, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or sha256(dest) != digest:
        print(f"   downloading {url}", flush=True)
        urllib.request.urlretrieve(url, dest)
    got = sha256(dest)
    if got != digest:
        dest.unlink()
        sys.exit(f"{dest.name}: SHA-256 {got} does not match the pinned {digest}")
    return dest


def setup(root, args):
    tc, env = root / "toolchain", dict(os.environ)
    sdk = tc / "android-sdk"
    if not (sdk / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat").exists():
        zip_path = download(*CMDLINE_TOOLS, tc / "dl" / "cmdline-tools.zip")
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(sdk / "cmdline-tools")
        (sdk / "cmdline-tools" / "cmdline-tools").rename(sdk / "cmdline-tools" / "latest")
    if not (sdk / "ndk" / NDK / "build" / "cmake" / "android.toolchain.cmake").exists():
        # Accepts the Android SDK licenses non-interactively, as CI does.
        env["JAVA_HOME"] = find_jdk17()
        subprocess.run([str(sdk / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat"),
                        f"--sdk_root={sdk}", "--install", f"ndk;{NDK}", "platforms;android-35",
                        "build-tools;35.0.0"], input="y\n" * 20, text=True, env=env, check=True,
                       stdout=subprocess.DEVNULL)
    subprocess.run(["rustup", "toolchain", "install", RUST, "--profile", "minimal",
                    "--target", "aarch64-linux-android"], check=True)
    tools = tc / "cargo-tools"
    if not all((tools / "bin" / f"{n}.exe").exists() for n in ("cargo-ndk", "cbindgen", "cargo-apk")):
        for extra in CARGO_TOOLS:
            subprocess.run(["cargo", f"+{RUST}", "install", "--locked", "--root", str(tools), *extra],
                           check=True)
    if not (tc / "libclang" / "libclang.dll").exists():
        wheel = download(*LIBCLANG, tc / "dl" / "libclang.whl")
        with zipfile.ZipFile(wheel) as z:
            member = next(n for n in z.namelist() if n.endswith("clang/native/libclang.dll"))
            (tc / "libclang").mkdir(parents=True, exist_ok=True)
            (tc / "libclang" / "libclang.dll").write_bytes(z.read(member))
    loader = tc / "openxr" / "libopenxr_loader.so"
    if not loader.exists():
        aar = download(*OPENXR_LOADER, tc / "dl" / "openxr_loader.aar")
        with zipfile.ZipFile(aar) as z:
            loader.parent.mkdir(parents=True, exist_ok=True)
            loader.write_bytes(z.read("prefab/modules/openxr_loader/libs/android.arm64-v8a/libopenxr_loader.so"))
    # The build scripts call python3, which on Windows is often only the Microsoft Store stub.
    shim = tc / "shim" / "python3"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', newline="\n")
    print(f"toolchain ready under {tc}")


def sync_sources(root, timer):
    research = root / "research"
    sh = git_shell("sh")
    if not research.exists():
        # First run: the full CI reconstruction from GitHub, including Granite and openvr.
        timer.run("reconstruct (first run, network)",
                  lambda: subprocess.run([sh, "tools/ci/fetch_sources.sh", research.as_posix()],
                                         cwd=REPO, check=True))
        return timer.run("sync manifest", lambda: sync_tree(research, research, root / "sync-backups"))
    granite = subprocess.run(["git", "-C", str(research / "pyrowave" / "Granite"), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    if granite != GRANITE_COMMIT:
        sys.exit(f"{research}/pyrowave/Granite is at {granite or 'nothing'}, not {GRANITE_COMMIT}; "
                 "delete the root's research directory to reconstruct it")
    stage = root / "stage"
    remove_tree(stage)
    env = dict(os.environ, Q3PW_BASE_REPOS=research.as_posix())
    timer.run("stage patched sources (local)",
              lambda: subprocess.run([sh, "tools/ci/fetch_sources.sh", stage.as_posix()], cwd=REPO,
                                     env=env, check=True, stdout=subprocess.DEVNULL))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    report = timer.run("content sync", lambda: sync_tree(stage, research, root / "sync-backups" / stamp))
    remove_tree(stage)
    for key in ("changed", "added", "removed", "backed_up"):
        if report[key]:
            shown = ", ".join(report[key][:8]) + (" ..." if len(report[key]) > 8 else "")
            print(f"   {key} {len(report[key])}: {shown}")
    if report["backed_up"]:
        print(f"   local edits in the build tree were saved under {root / 'sync-backups' / stamp}")
    return report


def build_client(root, env, log, timer):
    sh, bash = git_shell("sh"), git_shell("bash")
    deps = root / "research" / "ALVR-20.13.0" / "deps" / "android_openxr" / "arm64-v8a"
    deps.mkdir(parents=True, exist_ok=True)
    loader = root / "toolchain" / "openxr" / "libopenxr_loader.so"
    if not (deps / loader.name).exists() or sha256(deps / loader.name) != sha256(loader):
        shutil.copyfile(loader, deps / loader.name)
    timer.run("PyroWave Android", lambda: run([sh, "tools/build_pyrowave_android.sh"], env, log))
    timer.run("Android probes", lambda: (run([bash, "tools/pyrowave_android/build.sh"], env, log),
                                         run([bash, "tools/udptest/build.sh"], env, log)))
    timer.run("client APK (pyroclient + cargo)", lambda: run([sh, "tools/build_alvr_2013.sh"], env, log))


def build_streamer(root, env, log, timer):
    env = dict(env, XRWIRED_INPUTS=str(root))
    timer.run("PyroWave Windows", lambda: run(["cmd", "/c", r"tools\windows\build_pyrowave_pc.cmd", "interop"], env, log))
    timer.run("streamer + dashboard", lambda: run(["cmd", "/c", r"tools\windows\build_streamer.cmd"], env, log))


def source_identity():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO,
                                capture_output=True, text=True).stdout.strip())
    return head, dirty


def package(root, env, client, streamer, timer, timings):
    head, dirty = source_identity()
    out = root / "out" / (head[:7] + ("-dirty" if dirty else ""))
    alvr = root / "research" / "ALVR-20.13.0"
    sdk = Path(env["XRWIRED_ANDROID_SDK"])
    provenance = {"source_commit": head, "uncommitted_tracked_changes": dirty,
                  "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": "local Windows",
                  "rust": RUST, "ndk": NDK, "cmake_generator": env["Q3PW_CMAKE_GENERATOR"],
                  "note": "Local iteration build; not a reviewed CI artifact."}

    def android():
        d = out / "Quest3-Pyrowave-Android"
        if d.exists():
            remove_tree(d)
        d.mkdir(parents=True)
        shutil.copyfile(alvr / "build" / "alvr_client_android" / "alvr_client_android.apk", d / "Quest3-Pyrowave-dev.apk")
        shutil.copyfile(Path(env["XRWIRED_NDK"]) / "toolchains" / "llvm" / "prebuilt" / "windows-x86_64" / "sysroot" /
                        "usr" / "lib" / "aarch64-linux-android" / "libc++_shared.so", d / "libc++_shared.so")
        for rel in ci_android_outputs():
            shutil.copyfile(REPO / rel, d / Path(rel).name)
        shutil.copyfile(root / "research" / "pyrowave" / "build-android" / "libpyrowave-shared.so", d / "libpyrowave-shared.so")
        certs = subprocess.run([str(sdk / "build-tools" / "35.0.0" / "apksigner.bat"), "verify", "--print-certs",
                                str(d / "Quest3-Pyrowave-dev.apk")], capture_output=True, text=True, env=env)
        (d / "APK-CERTIFICATE.txt").write_text(certs.stdout)
        provenance["apk_stable_certificate"] = STABLE_CERT_SHA256 in certs.stdout
        write_sums(d)

    def windows():
        d = out / "Quest3-Pyrowave-Windows"
        if d.exists():
            remove_tree(d)
        d.mkdir(parents=True)
        shutil.make_archive(str(d / "Quest3-Pyrowave-Windows"), "zip", alvr / "build" / "alvr_streamer_windows")
        write_sums(d)

    if client:
        timer.run("package Android", android)
    if streamer:
        timer.run("package Windows", windows)
    provenance["timings_s"] = {label: round(sec, 1) for label, sec in timings.rows}
    (out / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2))
    return out, provenance


def ci_android_outputs(workflow=REPO / ".github" / "workflows" / "ci.yml"):
    """The probe binaries this branch's CI packages, read from its copy step.

    Branches build different probes, and old ones linger in the ignored output folders, so the
    list comes from the checked-out workflow instead of a copy that can drift.
    """
    for line in workflow.read_text(encoding="utf-8").splitlines():
        words = line.split()
        if words[:1] == ["cp"] and "tools/pyroclient/libpyroclient.so" in words:
            return [w for w in words[1:] if w.startswith("tools/")]
    sys.exit(f"no Android package copy step found in {workflow}")


def write_sums(directory):
    lines = [f"{sha256(p)}  {p.name}" for p in sorted(directory.iterdir()) if p.is_file() and p.name != "SHA256SUMS.txt"]
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")


def run_tests(root, env, log, timer):
    alvr = root / "research" / "ALVR-20.13.0"
    for spec in WINDOWS_TESTS:
        timer.run(f"cargo test {spec[0]}", lambda spec=spec: run(
            ["cargo", "test", "--release", "-p", *spec], env, log, cwd=alvr))
    timer.run("python suite", lambda: run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], env, log))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["setup", "sync", "build", "test"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="persistent build root, short to avoid Windows path limits (default %(default)s)")
    parser.add_argument("--client", action="store_true", help="build only the Quest client")
    parser.add_argument("--streamer", action="store_true", help="build only the Windows streamer")
    parser.add_argument("--no-sync", action="store_true", help="build the tree as it is")
    parser.add_argument("--keystore", help="release keystore (.p12); default: the stable key in the private workspace")
    parser.add_argument("--keystore-password-file")
    parser.add_argument("--allow-while-vr", action="store_true",
                        help="build even though SteamVR is running (it competes with the game for the CPU)")
    args = parser.parse_args(argv)
    vr = running_vr() if args.command in ("build", "test") else []
    if vr and not args.allow_while_vr:
        sys.exit(f"SteamVR is running ({', '.join(vr)}); not starting a heavy build while VR may be in use. "
                 "Use GitHub Actions, or pass --allow-while-vr if nobody is playing.")
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    timer = Timer()
    if args.command == "setup":
        return setup(root, args)
    if args.command == "sync":
        sync_sources(root, timer)
        print(timer.summary())
        return
    env, stable = build_env(root, args)
    log = root / "logs" / f"{args.command}-{time.strftime('%Y%m%dT%H%M%S')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if not args.no_sync:
        sync_sources(root, timer)
    if args.command == "test":
        run_tests(root, env, log, timer)
        print(timer.summary())
        return
    client = args.client or not args.streamer
    streamer = args.streamer or not args.client
    if client and not stable:
        print("WARNING: no stable signing key; this APK cannot upgrade a CI-signed install")
    if client:
        build_client(root, env, log, timer)
    if streamer:
        build_streamer(root, env, log, timer)
    out, provenance = package(root, env, client, streamer, timer, timer)
    print(timer.summary())
    print(f"outputs: {out}  (log {log})")
    if client and not provenance.get("apk_stable_certificate"):
        print("note: the APK is not signed with the stable CI certificate")


if __name__ == "__main__":
    main()
