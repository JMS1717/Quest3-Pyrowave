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

Matching cloud builds, packaged native identity against `.42`, and a controlled
Quest comparison are pending. No performance gain or sustained120 claim is made.
