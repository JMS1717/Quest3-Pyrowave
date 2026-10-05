# LOW-priority asynchronous eye-copy screen

**Keep synchronous direct eye copies as the default.** The October 5 `.44`
screen confirmed that asynchronous copies remove about 1 ms of CPU waiting,
but did not establish improved delivery or frame pacing. This is a new
combination: the earlier asynchronous screens predated LOW decode priority.

Matching `.44` APK/server builds and production tests passed. Native libraries
are byte-identical to GPU-verified `.42`, so its exact small/native readbacks
remain applicable. Source and codec were 2080×2208/eye, confirmed runtime 120 Hz,
1000 Mbps, 4:2:0, Haar/Compute, no foveation, LOW decode priority and one USB/TCP
worker. Selection wait was 4 ms, packet grace zero; event/ready/release/handoff
experiments and stage diagnostics were off. Async polling used zero wait.

The ABBA sequence used 3 seconds settling and 12 seconds measurement per arm,
with a client restart between arms. Both arms used the same continuous native
quality chart with a changing 10 Hz content counter. Each private screenshot
was taken after its measured window and GPU endpoint read.

| Mode | Eye completions/s | Client FPS p1 | Eye-render CPU p50/p95 ms | Async observed completion p50/p95 ms | Pending deferrals |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sync | 117.57 | 60.00 | 1.62 / 2.09 | — | 0 |
| Async | 116.57 | 60.00 | 0.60 / 0.79 | 8.21 / 9.12 | 5 |
| Async | 117.44 | 60.00 | 0.58 / 0.76 | 8.21 / 8.81 | 10 |
| Sync | 116.63 | 60.00 | 1.64 / 2.28 | — | 0 |

The candidate produced 1,389 and 1,458 positive completion-observed samples,
with exclusively direct copies, zero staging and no recorded fence failure.
This establishes actual asynchronous execution, beyond the property readback.
The approximately 8.21 ms completion observation is **wall time until a later
nonblocking poll**. It is not GPU copy time or measured extra optical latency.
The existing source AHB lease remains held while the copy is pending.

CPU time fell from approximately 1.63 to 0.59 ms. Delivery overlaps the controls,
p1 remains near 60 and decode-to-fence medians remain around 8 ms. The candidate
also reports 5/10 pending deferrals; the controls report zero. Saving CPU wait
alone has not removed frame selection/presentation misses. No unchanged repeat
or longer acceptance run is justified by this screen.

All four compositor images retained upright text and correct LEFT/RIGHT mapping.
Both eyes showed changing counters 154/503/851/1199, each present in the source
upload record. These endpoints reject a permanently frozen image across arms;
they do not establish continuous frame identity, pose correctness or headset
acceptance. GPU endpoints were 640 MHz throughout, battery temperature 40°C,
thermal status zero and AC power. Temporary settings/proximity and the original
session restored without errors; Virtual Desktop registration was preserved.

Restart and screenshot overhead, warm state, interval boundaries and stationary
latency estimates remain confounds. This is not sustained 120 FPS or optical
motion-to-photon. Next investigate scheduling and a presenter that preserves
decoded content/pose ownership, rather than increasing bitrate or enabling
async by default. [Metrics and exact package provenance](../results/ASYNC-LOW-NATIVE120-2026-10-05.json).
