# Fast local builds

Measured October 6, 2026 on source `5946281`. A full CI run takes about **20 minutes**. The same
APK and Windows streamer build locally in **under 3 minutes cold** and **30 seconds to 2 minutes**
after an edit. Use local builds to iterate. Reviewed CI builds remain the evidence for releases and
published measurements; see [When to use which](#when-to-use-which).

## Why CI takes about 20 minutes

Run 37410262620 (`5946281`, all jobs passed) took 20.6 minutes. Most of that is the Windows
`streamer` job, which waits about 3 minutes for `tests` and `publication_tests` and then runs for
16.6 minutes on a 4-vCPU runner:

| Streamer job step | Time | Why |
| --- | ---: | --- |
| Reconstruct sources | 1.1 min | Shallow clones of ALVR, PyroWave, Granite and their submodules |
| Restore cache | 2.0 min | 3.6 GB download (20 s), then 100 s of extraction |
| PyroWave DLL | 1.0 min | The CMake build directory is not cached |
| Streamer + dashboard | 4.8 min | Every ALVR crate recompiles; see below |
| Windows tests | 6.6 min | Seven separate `cargo test` runs; see below |

- **The Rust cache only covers third-party crates.** Each run clones the sources fresh, so every
  file gets a new timestamp. Cargo then treats every ALVR workspace crate as changed, even if its
  contents are identical to the cached build.
- **The test step recompiles the same crates repeatedly.** Each `cargo test -p <crate>` resolves
  features for that one package. `alvr_session` (about 30 s per build there) and its dependents
  recompile in almost every invocation. `alvr_server_core` alone took 2m15s.
- **Runs are duplicated and over-cached.** Each push here started a `pull_request` run plus a
  manually dispatched signed run, and superseded runs were cancelled by hand. `publication_tests`
  saved a new cache entry of about 0.5 GB on every commit of every branch. That put the repository
  at 10.5 GB, over GitHub's 10 GB limit, so caches get evicted. If the 3.5 GB Windows cache is
  evicted, the next run builds everything from scratch.

The Android `client` job takes only about 4 minutes. It finishes long before the streamer.

## Local timings

PC: 8-core/16-thread desktop CPU, 32 GB RAM, NVMe. Root `C:\q3pw\fast`.

| Step | Time |
| --- | ---: |
| One-time `setup`: Android SDK/NDK, cargo tools, libclang | about 3 min |
| First source reconstruction (network, once) | 78 s |
| Cold Android build: PyroWave 14 s, probes 2 s, APK 76 s | 92 s |
| Cold Windows build: PyroWave 12 s, streamer + dashboard 142 s | 154 s |
| Rebuild with no source change (both artifacts) | 39 s |
| Edit one client crate (`alvr_client_openxr`), signed APK | 28 s |
| Edit `alvr_session` settings, which rebuilds every crate on both sides | 2 min 5 s |
| CI's Windows Rust tests (133 tests) plus the Python suite: cold / warm | 184 s / 3 s |

From an empty root, `setup` (81 s) followed by the first `build` (5 min 39 s, including 90 s of
network reconstruction) took 7 minutes. The cargo registry was already populated; an empty one
adds the crate downloads.

APK packaging and signing account for about 20 s of every client build. The remainder of a
no-change build is Cargo and CMake confirming that nothing changed.

## Usage

```powershell
python tools/local/fast_build.py setup               # once; about 1.2 GB of downloads
python tools/local/fast_build.py build               # client APK + Windows streamer
python tools/local/fast_build.py build --client      # or --streamer
python tools/local/fast_build.py test                # CI's Windows Rust tests + Python suite
```

Requirements: Git for Windows, Python 3.12 or newer, Rustup, CMake and Ninja on `PATH`, JDK 17,
and a Visual Studio with the x64 C++ tools and Windows SDK. `setup` installs the pinned Rust
toolchain and Android target. It installs everything else under `<root>\toolchain`, and verifies
the SHA-256 of each download. It accepts the Android SDK licenses non-interactively, as CI does.
Nothing needs administrator rights.

Outputs go to `<root>\out\<commit>[-dirty]\`, in CI's layout: `Quest3-Pyrowave-Android\` (APK,
native libraries, probes, `APK-CERTIFICATE.txt`, `SHA256SUMS.txt`) and
`Quest3-Pyrowave-Windows\Quest3-Pyrowave-Windows.zip`. `PROVENANCE.json` records the source
commit, whether tracked files had uncommitted changes, the toolchain and step timings. The
streamer is also ready to run in place at `<root>\research\ALVR-20.13.0\build\alvr_streamer_windows`.
Logs go to `<root>\logs\`.

**Signing.** When the stable key exists in the private workspace (`workspace\keys\quest3-release`
beside this repo), or `CARGO_APK_RELEASE_KEYSTORE` is set, the APK is signed with it. The local
APK's certificate matches CI's (`2f2c5b3b…6779`), so `adb install -r` can upgrade a CI-built install
in either direction. Without the key, the build prints a warning: the APK is debug-signed and
Android will refuse it as an update. Keys never enter the repo or the build root.

**Safety.** `build` and `test` refuse to start while `vrserver.exe` or `vrcompositor.exe` is
running (`--allow-while-vr` overrides this). Compilers run at below-normal priority. Installing or
launching a build on the headset still needs current hardware authorization ([AGENTS.md](../AGENTS.md)).

## How the incremental build stays correct

- **Same recipe as CI.** The build steps are CI's own scripts: `build_pyrowave_android.sh`,
  `pyrowave_android/build.sh`, `udptest/build.sh`, `build_alvr_2013.sh`, `build_pyrowave_pc.cmd
  interop` and `build_streamer.cmd`. The pinned Rust 1.97.1, NDK 27.2.12479018, platform 35,
  build tools 35.0.0, cargo-ndk, cbindgen, cargo-apk and OpenXR loader are also the same.
- **Content sync instead of a fresh clone.** The first `build` runs `tools/ci/fetch_sources.sh`
  into `<root>\research`. Later builds stage the patched trees again with
  `Q3PW_BASE_REPOS=<root>\research`. This fetches the pinned base commits from the local clones and
  skips the unpatched submodules (Granite, openvr), so staging takes about 1.3 s instead of 70 s.
  `sync_tree` then copies only the files whose bytes differ. Unchanged files keep their timestamps,
  so Cargo, CMake and Ninja rebuild only what the edit affects. Files deleted from the recipe are
  removed. Build outputs (`target\`, `build*\`, copied runtime libraries) are never examined.
- **No silent loss of edits.** A file changed directly in the build tree is copied to
  `<root>\sync-backups\<time>\` before the sync restores the recipe version. Edit the repo's patches
  and helpers, not the build tree.
- **Files the build rewrites.** Cargo rewrites `Cargo.lock`, because the patch still lists
  `pyro.30` workspace versions. The build also recompiles the FFE `.cso`, and the patch's copy is
  stale while the compiled shader is identical to CI's (`15a04a00…`). The sync leaves these two
  until the patch changes them. `compile_foveation_shader.ps1` now replaces the `.cso` only when its
  bytes change, so an unchanged shader no longer recompiles the server's C++.
- **Granite pin check.** Every sync confirms that Granite is still at `842d9d5`.
- **Tests.** `tests/test_local_sync.py` covers changed, unchanged, added and removed files, build
  outputs, git metadata, submodules, backups and the build-rewritten files.

### Differences from CI artifacts

- **APK native libraries embed this PC's build paths** (source paths in assert and panic strings,
  and debug info), so they are not byte-identical to CI's. With symbols and the build ID stripped,
  `libpyrowave-shared.so` is byte-identical to CI's. Recompiling `libpyroclient.so` with
  `-ffile-prefix-map` mapping this PC's paths to CI's also makes the stripped library
  byte-identical to CI's (`cb7b9ef8…`). The local code is therefore the same; only the paths differ.
  `libalvr_client_openxr.so` differs in its read-only data size by a few hundred bytes, consistent
  with Cargo registry paths in panic messages; it was not path-normalized.
- **The local streamer was built with Visual Studio 2019 (MSVC 14.29)**, because this PC's VS2022
  has no C++ tools. CI uses VS2022, so the Windows binaries differ. Installing the VS2022 Build Tools
  with `Microsoft.VisualStudio.Workload.VCTools` and `Microsoft.VisualStudio.Component.VC.ATL`
  (requires administrator rights) restores compiler parity. `fast_build.py` picks VS2022
  automatically once its C++ tools exist.

## When to use which

| Need | Use |
| --- | --- |
| Compile errors, unit tests, quick fixes | Local `build` / `test` |
| Iterating on a headset experiment (with hardware authorization) | Local build; record its `PROVENANCE.json` and APK SHA-256 with the evidence |
| Measurements that will be published, release candidates, "matching reviewed pair" claims | CI signed build with every job passing, as before |
| The owner is playing VR | GitHub Actions (`build` refuses while SteamVR is running) |

A result measured on a local build should say so. Repeat it on the matching CI build before it
becomes a public claim.

## CI changes

Made on this branch:

- `concurrency`: a newer push or dispatch on the same non-main branch and trigger cancels the
  unfinished run.
- `publication_tests` restores its cache everywhere but saves it only from `main`, like the build
  caches. This stops the cache churn described above.

Recommended, not made. Each changes when or how tests gate builds, so it needs the owner's decision:

1. Start `client` and `streamer` without `needs: [tests, publication_tests]`, and rely on reviews
   requiring every job to pass. Saves about 3 minutes.
2. Move the Windows `cargo test` step into its own parallel job, which takes it off the critical
   path (expected saving 4–6 minutes). Or run the five `--lib` packages as one `cargo test`
   invocation. Measured locally from a cold target on `main`'s sources, the seven separate runs
   took 226 s, versus 175 s for one five-package run plus the `server_io` and `dashboard` runs
   (−23%), with the same tests passing. Feature unification differs slightly in the combined run.
3. In CI, restore cached timestamps for files whose content matches the cached build, using the same
   manifest as `sync_tree`, so unchanged workspace crates are reused. Before relying on it, verify
   that unchanged sources produce byte-identical native libraries with and without it.
4. Regenerate `Cargo.lock` and the FFE `.cso` in `quest3-alvr.patch`, so builds stop rewriting them.
5. Dispatch a signed run or rely on the `pull_request` run, not both, for each push.
