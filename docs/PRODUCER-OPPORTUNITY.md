# Producer preparation opportunity

**A complete next packet usually arrives while the current native call is still
running. Investigate bounded CPU pre-recording; no FPS gain has been measured.**
The October 5 `.45` diagnostic preserves synchronous producer completion and
all output leases. It adds observations behind a default-off property, rather
than allowing another GPU submission.

## Reviewed matching build

The [experimental source](https://github.com/JMS1717/Quest3-Pyrowave/commit/60799c84f1507551de2cf07fbc8c7be45fed0260)
and [matching Actions build](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37282120519)
passed all four jobs. Linux ran 48 actual production tests, including eight cases
for the observation helper. The matching APK/server hashes, signing certificate,
embedded versions and diagnostic markers were reviewed. All three native libraries
are byte-identical to GPU-verified `.42`/`.44`, permitting reuse of their exact
small/native-size readback evidence. No native GPU code or shader changed.

The [probe documentation and replay tooling](https://github.com/JMS1717/Quest3-Pyrowave/blob/experiment/producer-opportunity/docs/PRODUCER-PROBE.md)
live on the experimental branch. Main's default decoder remains unchanged.

## One short diagnostic

Source and encode were 2080 × 2208 per eye, at runtime-confirmed 120 Hz,
1000 Mbps, 4:2:0 and Haar/Compute, without foveation. LOW queue priority and
one USB/TCP worker were active; the selection wait was 4 ms. Ready/release,
async eye copy, handoff and event waiting were off. The same native quality chart
had a changing 10 Hz source counter. Measurement lasted 12 seconds after 3 seconds
settling. Actual `enabled=1`, source coverage, clocks, runtime and direct-only
copies were verified.

| Observation | Recorded value |
| --- | ---: |
| Complete aggregate rows / native calls | 12 / 1440 |
| Latest next packet present | 1376 / 1440 |
| Ready before native call | 2 |
| Arrived during native call | 1365 |
| Arrived after return, excluded from overlap | 9 |
| Remaining native-call wall time, summed / maximum | 7119.129 ms / 8.200 ms |
| Mean possible overlap per native call | 4.944 ms |
| Output headroom at post-return snapshot | 1422 / 1440 |
| CPU recording p50 / p99 | 0.561 / 1.061 ms |

Packet availability is timestamped **after** the payload copy. Thus most calls
have a usable successor well before return, rather than only a partially received
frame. This addresses the first prerequisite for hiding CPU preparation under
preceding work. It does not identify how much of the remaining call is GPU wait,
nor whether a free output existed when the packet arrived. Headroom and overlap
were separate observations; they cannot be combined into a reservation guarantee.
The latest pending packet may have replaced an earlier arrival. Aggregate rows
can straddle measurement edges.

Context only: eye completion proxy 117.52/s, instantaneous FPS p1~60, recording
median 0.561 ms, native completion median 7.992 ms and GPU decode median 5.892 ms.
The probe changes timing and adds lock reads; these values are not an A/B result
or performance promotion. GPU endpoints were 640 MHz, battery 29–30°C, thermal
status zero and AC power. One private screenshot showed upright correct eyes and
source counter 156 in both eyes, verified in the source record. It does not prove
continuous content/pose pairing, sustained 120 Hz or optical latency.

## Smallest next architecture

Start with **at most one GPU submission plus one conditionally pre-recorded
successor**, on one worker. Reserve a genuinely free output and a separate
command buffer; gate Granite context recycling. Publish the current frame only
after actual completion. Submit the successor only after that completion and
query collection. Preserve all shared-plane/scratch dependencies and explicitly
account for prepared-but-unsubmitted work on failure or teardown.

Keep ingress latest-frame policy bounded. Report preparation time, any wait
between preparation and submission, actual GPU completion, superseding and eye
completion separately. Do not disguise a prepared queue wait as lower decode
latency or compare changed timing definitions against old metrics. Reject the
candidate if it adds stale queued work or regresses latency/pacing. The
[lifetime audit](PRODUCER-OVERLAP.md) remains a prerequisite; this architecture is
not implemented or GPU-validated by the diagnostic.

Original properties/proximity/session settings were restored with fresh readback,
the independent restorer exited without errors, the project driver was removed,
and Virtual Desktop remained registered. The reviewed `.45` development pair is
retained; no public release or default synchronization change was made.
[Sanitized measurements and hashes](../results/PRODUCER-OPPORTUNITY-NATIVE120-2026-10-05.json).
