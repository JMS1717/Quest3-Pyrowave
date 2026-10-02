# Games launch on the PC but fail to enter VR

For OpenXR games played through Quest3-Pyrowave, select **SteamVR as the active
Windows OpenXR runtime**. A working SteamVR dashboard does not establish that
an OpenXR game uses that runtime. Windows runtime selection and SteamVR's ALVR
add-on registration are separate settings.

In the October 2 owner playtest, Pavlov loaded Virtual Desktop's OpenXR runtime
while the headset was connected through PyroWave. Its current runtime log ended
with `xrGetSystem failed with XR_ERROR_FORM_FACTOR_UNAVAILABLE`. Switching the
active runtime to SteamVR and relaunching the game resolved that routing issue;
the owner subsequently reported positive visual feedback. This is one session,
not broad game compatibility or a performance comparison.

## Check and switch

Close the affected game. Use SteamVR's OpenXR settings to select its runtime,
approve Windows elevation if requested, then relaunch the game. SteamVR itself
does not need another restart just to change the runtime used by a new game.

Read the 64-bit selection from a normal PowerShell window:

```powershell
(Get-ItemProperty 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime
```

For a standard Steam install, the SteamVR manifest is:

```text
C:\Program Files (x86)\Steam\steamapps\common\SteamVR\steamxr_win64.json
```

Use your actual SteamVR installation path. Preserve the previous manifest path
before switching. Do not uninstall Virtual Desktop, stop its service or remove
its driver to resolve this setting.

If an application still selects another runtime, check whether its launcher sets
`XR_RUNTIME_JSON`: this process environment override takes precedence over the
registry. Restarting an already-running game is required to load a different
runtime. A 32-bit application's selection is separate under
`HKLM:\SOFTWARE\WOW6432Node\Khronos\OpenXR\1`; the recorded fix changed only
the 64-bit value.

These selection rules are defined by the
[Khronos OpenXR loader documentation](https://github.com/KhronosGroup/OpenXR-SDK-Source/blob/main/specification/loader/runtime.adoc#windows-active-runtime-location).

## Return to Virtual Desktop

Close the game before switching modes. Follow the
[SteamVR driver rollback steps](BUILD.md#install-and-rollback), keep VD's
registration/service intact, and connect through Virtual Desktop. If you
previously used VDXR, select that runtime again through its configuration UI or
restore the exact saved manifest path. VD can also use SteamVR; restore your
previous choice rather than assuming every VD setup needs VDXR.

Record which runtime the game actually loaded when reporting a launch failure.
Share only the relevant error lines, with private identifiers removed. The
active runtime cannot fix codec corruption, missing controller bindings or
insufficient rendering performance.
