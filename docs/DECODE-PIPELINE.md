# Full-resolution frame-rate investigation

At 120 Hz the frame period is 8.33 ms. Short .8 native USB captures at the
padded panel resolution (2080 x 2208 per eye), 4:2:0 and CDF 9/7 measured about
10.6–10.9 ms of GPU wavelet decode and 15.0–15.5 ms from recording through
completed decode/conversion. One TCP worker waits for the Vulkan fence before
taking another frame. That serial path cannot complete 120 frames per second
with those timings. The runtime accepts 120 Hz, but the delivered frame rate
clusters around 60 as missed display periods accumulate.

This is a measured decoder bottleneck, not evidence of a universal 60 FPS cap.
A previous 60% resolution UDP capture reached a ~120 FPS median with poorer
tail pacing. Increasing USB target bitrate from 1000 to 2500 Mbps did not
increase the full-resolution median FPS. At 3000 Mbps the stream stalled.
The reported GPU timing includes GPU scheduling/preemption; completion timing
also includes recording, conversion, submission and fence wait. Their difference
must not be presented as pure CPU overhead.

## Experimental independent TCP workers (.9)

The client can create two independent Vulkan decoder contexts, each with its
own command/fence resources, intermediate planes and three output buffers.
This overlaps CPU recording and fence waits. It cannot create extra GPU capacity
and may increase contention and memory consumption. The default remains one.
This applies to complete-frame TCP, including ALVR USB forwarding; UDP is unchanged.

To select two workers for a controlled trial:

```powershell
adb shell setprop debug.q3pw.decode_workers 2
adb shell am force-stop io.github.jms1717.quest3pyrowave
adb shell am start -n io.github.jms1717.quest3pyrowave/android.app.NativeActivity
```

Restore one worker using `adb shell setprop debug.q3pw.decode_workers 1` and
restart the client in the same way. The property is read when the TCP decoder
starts. The client logs `[Q3PW_WORKERS] TCP independent decoders=...`.
Only the exact value `2` enables two workers; other values choose one.

Both workers consume a shared latest-frame slot. Completed frames publish in
ingress order: an older worker cannot replace a newer frame, including after
the render loop has taken it. Tracking timestamps are not used as sequence
numbers because different game frames may share a tracking timestamp.
Incomplete/rejected frames never publish. The pending and currently leased
hardware buffers remain excluded from native writes. Frame-order and worker
selection regression checks run in the client-core CI suite.

Compare one and two workers at the same resolution, codec/path, scene, bitrate
and thermal state. Measure delivered FPS, completion/decode distributions,
skipped/superseded frames, image correctness, latency and thermals. A cloud build
or successful decode does not establish sustained 120 FPS or a latency advantage
over Virtual Desktop. Fragment decoding also needs reference-quality validation.
