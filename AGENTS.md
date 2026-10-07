# Working on Quest3-Pyrowave

Quest3-Pyrowave is a PCVR streamer for Meta Quest 3. It is an ALVR fork that decodes PyroWave
wavelet video on the headset's Adreno 740 GPU with Vulkan compute. The owner is **JMS1717**.

Read [docs/HANDOFF.md](docs/HANDOFF.md) for the current state, scorecard and next steps. Where an
older handoff disagrees with the owner's latest direct instructions, follow the owner.

## Goal

The owner's target is:

- a 3072 × 3216 per-eye PC render
- streamed at 2080 × 2208 per eye, at 207 Hz
- about 200-207 genuinely fresh displayed frames per second
- at 1000-1500 Mbit/s
- under 30 ms optical motion-to-photon

Constraints on that target:

- **Image:** no visible foveation; 4:2:0 is fine. Image quality matters: the owner compares
  against Virtual Desktop and notices pixelation and blocky colour.
- **Priorities:** fresh frames, pacing, correctness and latency come before headline bitrate or
  refresh numbers.
- **Buffering:** small, bounded buffering is acceptable when measurements justify it.

**Current priorities (owner, October 7 evening, after playing `.65`).** These override the
order above:

1. Finish the network stack: Wi-Fi without stutter, wired with headroom for 2000 Mbit/s.
2. Then **sharpness and clarity**, including how bitrate is used and colour. The owner wants an
   image that looks above native and colour closer to 4:4:4. 4:4:4 is welcome if it can be made
   affordable. Virtual Desktop is the reference.
3. Latency after that. It is noticeable, but not the first priority.
4. Later: an interface and settings revamp. Measured profiles for USB and Wi-Fi, changes that
   apply without manual relaunches, a status view, and a simpler install. Also sustained-play,
   game-quality and nightly regression testing.

Keep finding the real bottleneck and rewriting what is slow; that is what has produced the gains
so far. The plan is [docs/PLAN.md](docs/PLAN.md).

## How to work

- **Implement, measure, decide.** Don't stop at analysis. Build the change, benchmark it against
  a baseline, and keep it, make it opt-in or reject it on the evidence.
- **Work autonomously.** Long sessions are welcome. There's no rush; prefer doing it properly.
  You may redesign tests, tools, build workflow and architecture when the evidence supports it.
  Earlier scripts and experiment sequences are not mandatory.
- **Land good results.** Promote proven wins to defaults, fold good branches together, and keep
  the PR stack short.
- **Release at good points.** Cut a pre-release when the stack is a clear step forward.
  - Update and improve the README and add `docs/RELEASE-<tag>.md` with every release.
  - Publish a local build of a clean, committed and pushed tree, after in-headset screenshots
    of that exact build look correct.
- **Keep the record current.** Update docs/HANDOFF.md at milestones with what changed, what was
  measured, what was rejected and what comes next. Write plain, specific docs with numbers and
  conditions.

## Hardware testing

Live testing on the owner's Quest 3 and PC is allowed. Before touching the hardware, check that
the owner isn't using it.

- **Owner in session:** if Virtual Desktop, SteamVR or a game is in the foreground, wait or work
  offline.
- **No per-session permission needed:** the owner doesn't have to re-authorize every session.
- **Absent owner:** the absent owner can't replug cables or put the headset on, so plan for
  recovery without them.

Leave the system as you found it:

- **Virtual Desktop.** Preserve its installation, registration and service. Don't leave this
  project's driver registered or SteamVR running after a test.
- **Settings.** Snapshot every headset property and session value you change. Restore each one
  with readback, including on failure.
- **Device-side changes.** Release any proximity or wake hold afterwards.
- **Foreground use.** Never kill or reconfigure a session the owner is using.

**Watch for these Quest 3 behaviours:**

- **Don't use `adb reconnect`.** Mid-stream it once left adb "offline" until a physical replug.
  To simulate an unplug, restart the PC's adb server instead (`adb kill-server`, then
  `adb start-server`).
- **The panel can get pinned at 72 Hz.**
  - HorizonOS writes `debug.oculus.refreshRate=72` itself when a VR app starts with the property
    empty.
  - A leftover value pins the panel.
  - Restore exactly the value you found, and verify the effective display period after every
    client relaunch. Relaunches sometimes come up at 72 Hz.
- **Unworn headsets read as unmounted.** Video still plays, but tracking, views and statistics
  stop. Hold proximity during tests and release it afterwards.
- **The memory clock is not controllable.** It moves between 2092, 2736 and 3196 MHz from session
  to session and shifts results by up to about 12 FPS.
  - Record it (VrApi `Mem=`).
  - Reject or separate measurement windows where it changed.
- **GPU clock.** The server's `quest3_max_gpu_clock` (690 MHz) makes decode-bound results
  repeatable. Use it for high-refresh work.
- **USB cannot be reset remotely.** This PC session usually isn't admin, so it can't reset the
  USB device. Don't rely on that for recovery.

## Measuring

Keep exploration windows short:

- 3-5 s settle, then a 10-12 s measurement, never more than about 15 s per exploration window.
- Use ABBA ordering where possible.
- Restart the client between arms when a setting is read at stream start.

**Fresh FPS** means distinct decoded frames actually shown: the client's `Q3PW_FRESH` taken count.

- Not VrApi FPS.
- Not ALVR's frame counter.
- Not the selected refresh rate.

Record these for every window:

- fresh FPS
- superseded and empty selections
- GPU decode and fence times
- eye-pass time
- GPU and memory clocks
- the effective display period

**Keep these claims separate:**

| Claim | Proves |
|---|---|
| runtime acceptance | the headset accepted the refresh rate |
| standalone decode budget | the decoder alone fits the frame time |
| short live screen | the stream works over a few seconds |
| sustained gameplay | the stream holds up in a real game |
| perceptual quality | it looks good in the headset |
| optical latency | the measured photon delay |

- A short screen can reject a candidate. It can't prove sustained FPS, thermals or latency.
- ALVR's latency figure is an estimate, never motion-to-photon. Optical latency needs a camera
  of at least 240 fps and 50 or more pairs.

The per-frame trace (`debug.q3pw.frame_trace=1`, `tools/quest3/frame_trace.py`) shows where frames
are lost: decode, publication, selection or presentation. Start with it before optimizing. See
[docs/FRAME-TRACE.md](docs/FRAME-TRACE.md) and [docs/BENCHMARKING.md](docs/BENCHMARKING.md).

## Building

Where things live:

- **ALVR changes:** `patches/quest3-alvr.patch`, applied to the pinned upstream in
  [sources.lock.json](sources.lock.json).
  - Edit a reconstructed ALVR tree, normalise line endings to LF, then regenerate the patch.
  - CI checks hunk counts.
- **Native bridge:** `tools/pyroclient/`.
- **Quest tools and analyzers:** `tools/quest3/`.
- **Python tests:** `tests/`.

How to build:

- **Build locally for everything, releases included:** `python tools/local/fast_build.py build`.
  - It takes about 1-3 min, against about 20 min on GitHub Actions
    ([docs/LOCAL-BUILD.md](docs/LOCAL-BUILD.md)).
  - It refuses to run while SteamVR is up. Pass `--allow-while-vr` only when nobody is playing.
  - Outputs land in `C:\q3pw\fast\out\<commit>[-dirty]\` in CI's layout:
    - the APK, `SHA256SUMS.txt` and `APK-CERTIFICATE.txt`
    - the Windows server zip
    - `PROVENANCE.json`
  - With the stable key in the private workspace, the APK is signed exactly as CI signs it.
- **Release builds:**
  - Build from a committed tree, so the output folder has no `-dirty` suffix.
  - Push that commit first and tag the release at it.
  - Attach the outputs, and record the commit and APK SHA-256 in the release notes.
- **GitHub Actions:** CI still runs on every PR push as a cross-check. Don't wait on it to keep
  working.
  - Fix any failures it finds.
  - A new push to the same branch cancels the run in progress.
- **Tests before pushing:**
  - `python -m pytest -q tests`
  - the Rust unit tests for any crate you touched

## Git and privacy

- **Commit identity:**
  `git -c user.name=JMS1717 -c user.email=43321848+JMS1717@users.noreply.github.com commit`.
- **No AI attribution:** no Co-Authored-By, session links or "Generated with" lines in commits
  or PRs.
- **Branches:** work on branches and open PRs. Never force-push.
  - Stack PRs only when a change truly depends on unmerged work.
  - Describe each PR with what changed, what was measured and what remains unverified.
- **Never commit:**
  - private captures or screenshots
  - session configurations
  - device serials or other identifiers
  - signing keys or tokens
- **Device serials** never go in logs or messages either.
- **Private material:** raw logs, captures, recovery snapshots and machine-specific state stay in
  the sibling `workspace/` and `handoff/` directories, outside the repo.

## Already tried; don't repeat without a new idea

Measured and rejected, or neutral:

- shared-memory CDF tiles
- FP16 decode
- fusing iDWT levels 4-2
- the earlier decode/dequant overlap experiment
- generic vector dequant
- fused dequant + Haar
- fusing final colour into conversion
- tuning mode 6 (paired chroma)
- LPAC queues
- Haar H2
- transposed bit-plane dequant
- server phase lock and pacing spin
- LOW decode priority above 120 Hz
- UBWC for the decoded buffer (at most about 0.2 ms)

**Opt-in, not defaults:**

- `release_fd` and `frame_hold_us`: [docs/FRAME-TRACE.md](docs/FRAME-TRACE.md)
- mode 6
- light foveation
- 4:4:4

Details are in docs/HANDOFF.md and the topic docs.
