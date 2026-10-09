# Decode queue priority

**At 120 Hz / 4:2:0 a LOW-priority decode queue lets the eye copy preempt
decode: about +3–5 displayed target FPS and half the lost frames. At 144 Hz it
costs about 10 FPS, so `.33` uses it only at ≤120 Hz with 4:2:0.**

## Problem

PyroWave decodes on a Vulkan queue and the ALVR client copies each eye into the
OpenXR swapchain with GLES. Both run at the GPU's default (medium) priority, so
a copy submitted while a decode is on the GPU waits for the whole decode. The
copy needs well under 1 ms of GPU time, but its CPU wall time (draw plus
`glFinish`) reached about 7.5 ms at p90. That makes it miss the compositor
deadline and repeat the previous frame.

wgpu-hal creates the GLES context without `EGL_IMG_context_priority`, so raising
the copy's priority would mean patching wgpu. Lowering decode is local:
pyroclient creates its queue with `VkDeviceQueueGlobalPriorityCreateInfoKHR`
LOW when the driver exposes `VK_KHR_global_priority` or
`VK_EXT_global_priority`. If device creation rejects it, pyroclient retries at
default priority. Quest 3 (Adreno 740) accepts it through
`VK_KHR_global_priority`.

## Measurements

The priority is fixed when the decoder's Vulkan device is created, so each block
relaunched the client: 5 s settle excluded, 20 s measured, order ABBAABBAAB.
The source was a stationary procedural chart at 2080x2208 per eye, over USB/TCP
at 1000 Mbps. Paired values compare adjacent blocks.
([results](../results/DECODE-PRIORITY-AB-2026-10-04.json))

| Session | Arm | Displayed target FPS | Lost frames/s | Eye-copy CPU p90 | GPU decode p90 |
| --- | --- | --- | --- | --- | --- |
| `.32`, 120 Hz, 4:2:0 | default | 114.3 | 5.8 | 7.51 ms | 5.88 ms |
| | LOW | **117.7** (5/5 pairs) | **2.8** | **1.81 ms** | 6.92 ms |
| `.32`, 144 Hz, 4:2:0 | default | **135.0** | **10.2** | 6.74 ms | 5.44 ms |
| | LOW | 124.7 (0/3 pairs) | 20.2 | 2.25 ms | 6.58 ms |
| `.33`, 120 Hz, 4:2:0 | auto (chose LOW) | **115.7** (4/5 pairs) | **4.8** | **1.87 ms** | 6.83 ms |
| | forced default | 110.6 | 9.6 | 7.41 ms | 5.89 ms |

Preempted decode takes about 1 ms longer at p90. At 120 Hz (8.3 ms per frame)
there is room for that. At 144 Hz (6.9 ms) decode plus convert already fills
most of the frame, and the extra time costs whole frames. Median estimated
pipeline latency was lower with LOW in both 120 Hz sessions (56.3 → 54.3 and
57.4 → 52.2 ms). That statistic is quantized to display periods and drifts, so
this is not a latency claim.

## Control

| Property | Read | Effect |
| --- | --- | --- |
| `debug.q3pw.decode_priority` | at decoder creation | unset/other: LOW only for 4:2:0 at ≤120 Hz (`.112`-`.114` used default at ≤90 Hz; reverted in `.115`); `low`: always LOW; `default`: never |

logcat `[Q3PW_PRIORITY]` reports the policy decision (refresh rate, chroma,
choice) and what the driver applied.

## 207 Hz with mode 5 (October 7)

Mode 5 cut decode to about 2.6 ms of the 4.83 ms period, about the share at which LOW won at
120 Hz, and the eye copy's CPU span was 4.24 ms p50 (5.19 ms p90), so LOW was retried. Haar mode
5, 700 Mbps, 2080x2208 per eye from 3072x3216, 60 deg/s pan, headset awake, 12 s windows:

| Arm | Fresh FPS | Lost/s | GPU decode p50 | Fence p50 |
| --- | --- | --- | --- | --- |
| default (auto chose default above 120 Hz) | 192.9 | 14.4 | 2.67 ms | 4.64 ms |
| `decode_priority=low` | 163.5 | 43.8 | 4.12 ms | 5.73 ms |
| `decode_priority=low` | 165.3 | 41.8 | 3.49 ms | 5.70 ms |

The closing default block was interrupted (the PC ran short of memory and the run was stopped);
the same build's default measured 195.4-196.5 FPS in the four mode 5 blocks run just before.
Preempting decode at 207 Hz costs about 30 fresh FPS: keep the automatic policy.

## Full size at 80 and 90 Hz (October 9, `.111`-`.114`)

Full 3072x3216 per eye, Haar, 1500 Mbps, SteamVR Home with the Library dashboard open, wired,
unworn, GPU level 7, 20 s blocks, priority forced by property.

| Refresh | Arm | Fresh FPS | GPU decode p50 | Fence p50 | VrApi stale/s |
| --- | --- | --- | --- | --- | --- |
| 90 Hz | default | 84.6, 83.4 | 4.9-5.1 ms | 6.7-7.1 ms | 0-4 |
| 90 Hz | LOW | 79.9, 79.3 | 6.9-7.1 ms | 10.7-11.0 ms | 0 |
| 80 Hz | LOW | 79.2, 79.3, 80.2, 78.9 | 6.7-10.4 ms | 9.6-11.7 ms | 0 |
| 80 Hz | default | 78.6, 78.0, 78.3 | 4.0-5.7 ms | 5.9-6.7 ms | 0-10 |

At 90 Hz LOW cannot keep up and default wins by about 4.5 FPS. At 80 Hz LOW keeps up, and
default only moves time around: ALVR's decoder stage falls from 12-18 to 7-8 ms, but its decoder
queue (1-7 against 2-3 ms) and client compositor (5-8 against 4.5 ms) grow, and total latency is no
lower (63-73 against 64-71 ms). `.112` switched the automatic policy to default at ≤90 Hz; `.115`
reverts that because the shipped Godlike profile runs at 80 Hz. For full size at 90 Hz, set
`decode_priority=default` by hand.

## Limits

Stationary chart on an unworn headset. Not gameplay, perceptual or sustained
thermal validation. Displayed target FPS is not optical FPS. Not measured:
4:4:4, bitrates other than 1000 Mbps, or refresh rates other than 120 and
144 Hz. The ≤120 Hz rule extrapolates from the 120 Hz headroom. The 144 Hz
session stopped after 7 of 10 blocks when one relaunch did not reconnect.
