# Optical latency measurement

Every latency figure so far is ALVR's own estimate (about 44 ms at 120 Hz). Nothing optical has
been measured. This procedure produces the first physical composition-to-photon number. It needs
the owner's hardware go-ahead (AGENTS.md).

## What is measured

When `video.pyrowave.latency_stamp` is on, the Windows server draws a five-digit millisecond clock
and two frame-parity squares just below the center of each eye. The stamp is drawn after
SteamVR composition and before color correction, foveation and YCbCr conversion. The clock is
`floor(QPC * 1000 / frequency) mod 100000`. `tools/quest3/latency_clock.py show` displays the
same clock on the PC monitor. One slow-motion video that shows both the monitor and a headset
lens therefore gives:

    composition-to-photon = monitor value - lens value + monitor display lag

This covers encode, transport, decode, presentation and the headset display. It excludes game
render and the tracking uplink, so motion-to-photon is larger by roughly those terms. The two
squares show frame-counter bits 0 and 1. On a high-speed video they reveal repeated frames (no
change) and skipped frames (jumps).

## Procedure

1. Enable the stamp: `python -m tools.quest3.control latency-stamp --state on`, then restart
   SteamVR.
2. On the streaming PC, run `python tools/quest3/latency_clock.py show`. It holds a 1 ms Windows
   timer only while it runs. Esc exits.
3. Hold a phone recording at 240 fps or faster so it sees the monitor clock and one lens.
   Keep the headset still and the scene static.
4. Step through the video frame by frame. For each frame where both values are legible, write
   `monitor,lens` into a CSV (a `monitor,lens` header and `#` comments are allowed).
5. Run `python tools/quest3/latency_clock.py analyze readings.csv --monitor-lag-ms <lag>`.
   Use the monitor's measured or published input-to-photon lag, and state which one was used.
   The tool reports median, p90, min and max, and rejects pairs that wrap to implausible values.
6. Disable the stamp with `latency-stamp --state off`, restart SteamVR, and confirm the readback.

Each video frame is accurate to about one camera frame (4.2 ms at 240 fps) plus a monitor
refresh. Collect at least 50 pairs across several seconds and report the median and p90 with
the rate, bitrate, foveation profile and fused-decode settings. Keep this result separate from
ALVR's estimate. Neither one replaces the other.
