# Quest 3 extended refresh

Primary reference: [Meta display refresh documentation](https://developers.meta.com/vr/documentation/unity/unity-set-disp-freq/), updated August 27, 2026.

HorizonOS v2.7+ supports integer 72–207 Hz on Quest 3 through standard refresh requests.
Quest 3S does not have these extended modes. Legacy enumeration can omit valid modes.
The APK therefore tests requests rather than adding a hard-coded capability claim.

On first session startup, the lobby tests 90,120,144,207,240 Hz. Each accepted request must
match `xrGetDisplayRefreshRateFB` and at least three consecutive `xrWaitFrame` periods within
0.5%, with a two-second settling deadline. It restores the session's original rate, then sends
confirmed capabilities to ALVR. A request failure or timeout is a recorded result. Reopen the
APK after changing the environment to run a fresh probe. Startup can briefly change lobby refresh.

## 240 Hz developer experiment

Above 207 Hz the panel requires display scaling: lower-resolution display payload followed by
hardware upscaling. Fine detail changes even with a full-chroma encoded stream. Keep these results
separate from full-resolution 207 Hz measurements.

The APK does not set the following properties. A developer can opt into a scoped experiment:

```powershell
python -m tools.quest3.refresh_scaling status
python -m tools.quest3.refresh_scaling enable --state results/local/scaling-before.json
# Reopen APK, inspect capabilities and capture effective period before streaming measurements.
python -m tools.quest3.refresh_scaling restore --state results/local/scaling-before.json
```

The helper verifies a Quest 3 device, saves both prior values, sets
`debug.oculus.forceDisplayScaling=1` and `debug.oculus.refreshRate=240`, and restores saved values.
This is an explicit system-setting change; a property value is not proof of scanout. Do not leave
the experiment enabled between benchmark sessions. No root, reboot, persistent property or clock
forcing is required by this recipe.

Thermal throttling can reduce the rate. `[Q3PW_EFFECTIVE]` records runtime/frame-period changes;
client FPS and dropped frames establish delivery separately. Physical optical latency requires
external instrumentation.
