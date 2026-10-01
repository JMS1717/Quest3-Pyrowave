# USB streaming

ALVR 20.13 includes the [native wired mode introduced in 20.12](https://github.com/alvr-org/ALVR/wiki/ALVR-wired-setup-%28ALVR-over-USB%29).
The dashboard forwards control/video TCP ports through ADB; no Meta PC runtime is needed.

1. Use a USB data cable and a USB 3 port. Enable Quest Developer Mode and accept USB debugging.
2. Start our dashboard and SteamVR, then enable **Devices → Wired Connection**.
3. This fork defaults **Connection → Wired Client Type** to Custom:
   `io.github.jms1717.quest3pyrowave`. Older fork builds must set that explicitly.
4. Use **PyroWave transport TCP**. Restart SteamVR after changing transport.
   In the new build, a wired peer automatically forces native/server video to TCP even if
   a UDP preset was selected. The standalone UDP video socket cannot use ADB forwarding.
5. Open **Quest3 PyroWave** on the headset. Confirm **client.wired** is Streaming.

For scripts, `python -m tools.quest3.control usb --enable` sets the distinct package and
both TCP settings, then enables the native wired peer. `--disable` removes only that
peer; it leaves TCP available for wireless use. Restart SteamVR when transport changes.
The server must find ADB on PATH or download its own platform tools beside the dashboard.
For a portable setup, put Google's platform-tools directory beside the dashboard.

Check `adb forward --list` for ports **9943** (control) and **9944** (stream), and verify
the server's active peer is **127.0.0.1**. A cable being plugged in does not prove video
uses USB. An ADB-over-Wi-Fi connection is also not USB evidence; use `adb devices -l`
to confirm a physical USB device. Disconnect or disable wired mode to return to Wi-Fi.

USB's advertised link rate is not ADB payload throughput. A USB 2 cable/port, ADB copies,
CPU load and TCP buffering can limit it. Measure this path rather than assuming 1000–2000
Mbps will work. USB removes the Wi-Fi hop; it does not remove Quest GPU wavelet decode,
hardware-buffer conversion, compositor work or their thermal limits. Match resolution,
refresh, chroma and scene when comparing USB and wireless latency.
