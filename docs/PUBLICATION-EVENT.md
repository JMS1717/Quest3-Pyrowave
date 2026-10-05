# Bounded publication notification experiment

The default Quest 3 path polls for a newly published frame in 50 µs sleeps, only
while decode is in flight, for at most the existing 4 ms selection budget (also
capped at half the display interval). This experiment replaces those sleeps with
a condition variable using the frame slot's existing publication mutex.

It is **off by default**. Native codec, image conversion, eye copy, resolution,
bitrate, chroma and foveation are unchanged. This is a CPU scheduling experiment;
a notification does not establish GPU completion or motion-to-photon latency.

## Ownership and eligibility

- The predicate and waiter registration use the same mutex as publication.
  A frame published before the wait is observed without parking; a publication
  during the wait wakes the consumer. Spurious wakes recheck the predicate.
- Waiting neither dequeues a frame nor changes its lease, release token or ready
  descriptor. The existing dequeue and checked GPU synchronization paths still
  perform those operations. A ready descriptor is attached before notification.
- Decode failure wakes the consumer. The original start time bounds every wake;
  neither a notification nor packet grace resets the deadline.
- Only Quest 3 PyroWave TCP with one decode worker is eligible. Other paths,
  including the optional two-worker path, retain the existing poll.
- The existing packet-grace setting remains zero by default. Opting into it
  still uses the original total wait budget.

## Controlled comparison

On the matching `.44` APK/server, set `debug.q3pw.frame_wait_event` to `0` for the
control or `1` for the candidate, then restart the client stream. Keep the same
scene, source coverage, 2080×2208 encode, runtime120, 1000 Mbps, 4:2:0, no foveation,
LOW decode priority, 4 ms wait, zero grace and synchronous direct-eye copy.
Use interleaved controls and candidates; keep stage diagnostics off.

`Q3PW_EVENT_WAIT_SETUP` reports the requested state. `Q3PW_EVENT_WAIT` reports
eligible calls, actual condition-variable waits, pending outputs seen and fallback
calls. A requested property alone does not prove the candidate executed.

Replay saved logs with `python -m tools.quest3.publication_wait client.log
--start <epoch> --end <epoch> --pid <recorded-client-pid>` (one command). Use
start/end timestamps on the logcat clock, or align its clock to your capture
first. The parser isolates that PID and a half-open capture interval, rejects
malformed counters, and keeps requested state separate from actual waits and
fallback. Its counters describe reported intervals, which can cross capture
edges; they are not per-frame timing percentiles or GPU completion proof.

Compare fresh eye completions, frame pacing/p1, decode and completion distributions,
selection time, superseding, latency estimates and health. Short screens can reject
a candidate; sustained gameplay and in-headset image acceptance remain separate.
Keep the current default unless repeated measured improvement justifies changing it.

## Verification status

Production frame-slot tests cover publication before/after waiter registration,
failure wakeup, spurious wakes, decode starting during grace, expired deadlines,
timeout, unchanged leases and once-only ready-FD transfer. Linux CI executes the
actual decoder library including Unix descriptor tests; Windows executes its
platform-compatible tests. Android compilation checks stream integration.

Linux CI passed all40 production decoder tests, including the nine new
notification/ownership cases. Android built successfully; the signed APK's three
native libraries are byte-for-byte identical to the reviewed `.42` pair, whose
default small/native GPU readbacks already passed. Windows matching build/regressions and the controlled Quest comparison also
passed their correctness/configuration gates. No performance gain or sustained120
claim is made. [Matching source/build](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37270657975).

## Quest result: keep disabled

All matching build jobs passed; signed APK/server versions, hashes, markers and
unchanged compiled FFE shader were checked. An ABBA screen used12-second windows
with2 seconds settling, runtime120, native2080×2208, 1000Mbps, Haar/Compute4:2:0,
no foveation, LOW decode priority, direct synchronous eye copy, one TCP worker,
4ms wait and zero grace. Decode stage diagnostics remained off. Source coverage,
active codec/geometry/foveation, clock alignment and each client PID were verified.

| Arm / event flag | Eye completions/s | GPU decode p50/p95 ms | Decode-to-fence p50/p95 ms | Client FPS p1 | Actual Condvar calls / pending seen |
| --- | --- | --- | --- | --- | --- |
| A / 0 | 118.28 | 6.58 / 6.79 | 8.04 / 8.26 | 60.0 | 0 / 0 |
| B / 1 | 115.82 | 6.37 / 7.33 | 8.08 / 9.46 | 60.0 | 223 / 166 |
| B / 1 | 116.03 | 6.52 / 7.34 | 8.09 / 9.38 | 60.0 | 194 / 142 |
| A / 0 | 115.54 | 5.88 / 7.33 | 8.00 / 9.48 | 60.0 | 0 / 0 |

The candidate executed223/194 actual condition-variable waits, observed166/142
pending outputs, and used zero fallbacks. Default controls reported zero event
calls. The worker info message was not mirrored into logcat; readback verified
the one-worker property in both arms, and the candidate's compiled API returns
an eligible wait only with one worker. This is execution proof, not GPU completion.

**No consistent delivery benefit. Keep the experiment off.** The first control
outperformed all later blocks; candidate delivery overlaps the final control.
Later GPU/completion tails grew in both candidate and control, so they do not
establish an event-specific regression. P1 remains near60 and sustained native120
is unmet. Removing polling alone has not removed the presentation bottleneck.
No unchanged repeat or longer sustained screen is justified by this result.

Four private compositor images, captured after the measured windows and endpoint
reads, retain upright chart/text, correct eye mapping and a readable overlay.
They establish neither optical latency nor in-headset quality acceptance. These
extra captures and restart phase can affect subsequent blocks. Temperatures stayed
30–33°C with thermal status0 and GPU endpoints640MHz; endpoints are not continuous
or per-app load measurements. Normal restoration had no errors and retained VD.
[Sanitized metrics, package identities and limitations](../results/PUBLICATION-EVENT-LIVE-2026-10-05.json).
