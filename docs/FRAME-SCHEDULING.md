# Frame-selection diagnostics

The `.26` release-fence screen removed much of the CPU eye-render wait but did
not increase fresh FPS. In the accepted OFF/ON/OFF capture, swapchain acquire/wait
p95 was **2.15/4.40/1.11 ms** and estimated display-queue p50 was
**17.64/32.09/25.80 ms**. These observations suggest investigating runtime feedback
and selection timing; they do not establish which event caused the FPS loss.
[Comparison and limitations](RELEASE-FENCE-EXPERIMENT.md).

The `.27` candidate adds diagnostics only. With `debug.q3pw.loop_probe=1`, Quest 3
PyroWave records a `[Q3PW_SELECTION]` window every 120 render selections. Exactly
one outcome applies to each selection:

| Outcome | Meaning |
| --- | --- |
| `held` | A protected frame was already selected before the runtime wait. |
| `immediate` | The first ready-queue poll returned a frame. |
| `late` | A later poll during the existing optional bounded wait returned a frame. |
| `empty` | No completed frame was selected despite the eye-copy path being available. |
| `copy_pending` | The previous eye copy prevented taking another source lease. |
| `no_decoder` | No decoder was available; reserved for diagnostic consistency. |

`select_mean_us`/`select_max_us` measure CPU time through the selection code,
including its optional existing wait and occasional selection bookkeeping.
The probe adds no queue, sleep, copy or GPU synchronization. It preserves frame
selection, the output leases, existing defaults and the synchronous baseline.
Both release-fence and bounded-wait experiments remain off by default.

`[Q3PW_LOOP]` retains its existing fields and adds CPU means for the actual
`xrWaitFrame` call, `xrBeginFrame`, stream/lobby render and frame work from the
pre-wait selection phase through `xrEndFrame`. The old `wait_mean_ms` also
includes bookkeeping immediately after the wait; use `wait_call_mean_ms` when
isolating the API call. Frame work excludes the earlier event-processing phase
and the final diagnostic log. Non-rendering runtime frames are not in these
windows. These timings overlap producer GPU work: **do not sum them with GPU
decode time** or call them motion-to-photon latency.

## Reading a saved capture

Enable the existing loop property only during an authorized, device-pinned test,
save its original value, and restart this client. Review a matching `.27`
APK/server pair before deployment. Keep resolution, scene, thermal state,
overlay and all decode settings identical. A short probe off/on/off comparison
should establish its overhead before using it to assess another experiment.

```powershell
python -m tools.quest3.scheduling frame-loop.log --start <capture-start-epoch-seconds> --end <capture-end-epoch-seconds> --pid <recorded-client-pid> --out scheduling.json
```

The reader checks process identity, time bounds, nonnegative finite timings and
outcome conservation. `.26` logs can yield `legacy_loop_only`; absent `.27`
fields remain unknown. It weights window means by their recorded sample counts.
Whole windows ending within the interval can start before it, so the outcome
totals must not be equated with exact capture-time counter deltas. Neither these
counts nor eye-copy counters certify unique optical presentations.

Start by distinguishing empty selections from copy deferrals, then compare
runtime wait/acquire/render phases and producer superseding over the same
capture. Do not promote a new selection policy from lower CPU time alone.

## First short screen (October 2)

Matching `.27` probe off/on/off captures delivered **108.65 / 107.97 / 106.12**
fresh submissions/s. In the probe-on logged windows, 1,800 display selections
included **1,601 immediate frames and 199 empty selections**, with no late,
held-frame, missing-decoder or pending-copy outcomes. Mean selection CPU time
was 69 microseconds; actual `xrWaitFrame` call time averaged 3.50 ms and the
recorded total loop work 8.30 ms. These CPU phases overlap producer GPU work.

This identifies an empty selection boundary in this window; it does not prove
why a producer completing around 120 frames/s misses display slots. The
probe-on decode completion and latency were higher than both controls, with
dynamic clocks and phase differences, so the screen does not establish
negligible probe overhead. Keep the probe off during normal play. The
[aggregate measurements](../results/FRAME-SCHEDULING-LIVE-2026-10-02.json)
include both controls, clocks, thermals and limits. Source coverage was verified;
the private reader was corrected and applied to saved logs after capture.
Neither sustained 120 FPS nor optical latency was accepted.

[![Support development](https://img.shields.io/badge/PayPal-Support%20development-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)
