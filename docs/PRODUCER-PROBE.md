# Producer opportunity diagnostic (.45 candidate)

This branch adds a default-off scheduling probe, not asynchronous decode.
No producer wait, output lease, queue policy, shader or native library changes.
Matching APK/server build and device observation are separate gates; no live
result is claimed yet. The [lifetime audit](PRODUCER-OVERLAP.md) explains why a
three-slot ring cannot simply replace the current wait.

`debug.q3pw.producer_probe=1` is sampled when the TCP decoder is created. The
probe enables only with one worker, synchronous native completion, no ready FD,
no release FD, no async eye copy and no decode handoff. Otherwise it logs
`enabled=0` and leaves the probe off. Restart the client to change it. These are
developer diagnostics, not a recommended user preset.

When enabled, complete-packet availability is timestamped after its copy into
the pending queue. One monotonic clock brackets each successful synchronous
native call. Immediately after return, the worker snapshots the latest pending
packet and the two protected output handles. It logs an aggregate after every
120 successful calls:

| Field | Meaning |
| --- | --- |
| `calls` / `pending` | Completed native calls / observations with a next pending packet |
| `ready_before` | Latest pending packet was usable before this native call began |
| `arrived_during` | Its usable time falls within this native call |
| `arrived_after` | Receive raced after native return; excluded from overlap |
| `overlap_sum_us` / `overlap_max_us` | Native-call wall time after next-packet availability, capped to the call |
| `call_sum_us` | Total native-call wall time in the aggregate |
| `headroom_at_snapshot` | Observations with fewer than three distinct non-null handles among leased, pending and this output |

This bounds a possible CPU preparation opportunity. It does **not** isolate GPU
waiting, Granite recycling, presentation deadlines or optical latency. Native
calls include recording, waiting and result processing. A pending packet may
be a later replacement; earlier arrivals are not reconstructed. The slot-state
snapshot occurs after native return and is separate from the packet snapshot.
Headroom and overlap aggregates are not proof they occurred together or that a
slot was free during GPU work. Reading state does not reserve a buffer.

The probe adds clocks and brief lock reads only when enabled, plus one log row
per 120 calls. Diagnose opportunity with it; assess performance with diagnostics
off, against the same matching build. Do not interpret diagnostic overhead or
an instrumented FPS change as an optimization.

The same Rust observation helper is copied into production by source
reconstruction. Eight CPU cases cover eligibility, late receive, missing payload, zero
overlap boundaries, capped pre-existing queue age, duplicate/null output handles,
interval reset and invalid clock bounds. Linux production tests also compile
the helper; the Android build checks its actual decoder integration.

Replay saved logcat with `python -m tools.quest3.producer_opportunity LOG --start
START_EPOCH --end END_EPOCH --pid CLIENT_PID`. Select the recorded process and
convert clock offsets consistently with the capture. The parser rejects missing
or contradictory evidence, and never treats absent probe rows as acceptance.
Its five CPU cases cover identity/window filtering, late ingress, interval
aggregation, malformed rows and timing/counter conservation. Aggregate intervals
can straddle capture boundaries; this is a scheduling diagnostic, not per-frame
latency percentiles.

After matching CI and artifact review, collect one short native120/1000/420/LOW
baseline with actual `enabled=1` and complete interval rows. Keep current native
completion/GPU/image checks and independent restoration. If packets usually
arrive after useful preparation time, do not build an overlap queue. If there is
repeatable opportunity, investigate conditional pre-recording with one GPU
submission before allowing two submitted decodes. No default promotion follows
from the probe alone.
