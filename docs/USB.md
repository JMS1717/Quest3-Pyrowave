# USB streaming

ALVR 20.13 includes the [native wired mode introduced in 20.12](https://github.com/alvr-org/ALVR/wiki/ALVR-wired-setup-%28ALVR-over-USB%29).
The dashboard forwards control/video TCP ports through ADB; no Meta PC runtime is needed.

1. Use a USB data cable and a USB 3 port. Enable Quest Developer Mode and accept USB debugging.
2. Start our dashboard and SteamVR, then enable **Devices → Wired Connection**.
3. This fork defaults **Connection → Wired Client Type** to Custom:
   `io.github.jms1717.quest3pyrowave`. Older fork builds must set that explicitly.
4. Use **PyroWave transport TCP**. Restart SteamVR after changing transport.
   A wired peer forces native/server video to TCP even if a UDP preset was selected.
   The standalone UDP video socket cannot use ADB forwarding.
5. Open **Quest3 PyroWave** on the headset. Confirm **client.wired** is Streaming.

For scripts, `python -m tools.quest3.control usb --enable` sets the distinct package and
both TCP settings, then enables the native wired peer. `--disable` removes only that
peer; it leaves TCP available for wireless use. Restart SteamVR when transport changes.
The server must find ADB on PATH or download its own platform tools beside the dashboard.
For a portable setup, put Google's platform-tools directory beside the dashboard.

## Which device wired mode uses

Since `.63` (alpha.9), wired mode uses only an **online adb device attached over USB**:

- Wireless-debugging entries (`ip:port`, or mDNS `adb-…._adb-tls-connect._tcp`) are skipped.
  Before `.63`, the headset's own Wi-Fi adb address could be taken for a wired device and the
  stream tunnelled through adb over Wi-Fi.
- Offline and unauthorized entries are skipped, so one of those listed first no longer blocks
  connecting.
- When no USB device is ready, the server goes on to manual IPs and discovery and can stream over
  the network. Before `.63`, the wired entry stopped it from ever trying the network.

## Parallel wired video connections

adb forwards TCP only, and one forwarded connection moves a frame burst at about 1.7-2.4 Gbit/s.
At 1000-1500 Mbit/s and 207 Hz that is most of a 4.83 ms frame interval. So over USB the server
splits each complete PyroWave frame into contiguous slices and writes them in parallel on
dedicated connections. The client reassembles the frame and decodes it as if it had arrived on
ALVR's stream connection.

- **Setting:** **Wired video connections** (`video.pyrowave.wired_video_connections`), 0-4,
  default **2**. **0** sends video on ALVR's stream connection, as before. It is read at stream
  start; changing it while streaming reconnects ([SETTINGS-APPLY.md](SETTINGS-APPLY.md)).
- **Ports:** TCP 9950-9953, one per connection, listening on the headset's loopback. In wired
  mode the server forwards all four through adb, whatever the setting.
- **Fallback at start:** the client answers each connection with a 4-byte hello (`PWT1`). If any
  connection fails to connect within 1 s or gives no hello within 2 s, video stays on the stream
  socket for that session.
- **Numbered slices (`.64`, on main, not yet released):** each slice has a 48-byte header with
  the server's frame sequence number (bytes 28..32). The client assembles frames by that number
  and rejects overlapping or mismatched slices. A server older than `.64` sends sequence 0; the
  client then keys frames by timestamp. `.64` replaces alpha.9's skip of frames with a repeated
  timestamp.
- **Fallback mid-stream (`.64`):** each writer has a 1 s write timeout. A connection that stalls or
  fails is treated as lost, and video goes back to the stream socket for the rest of the session.
- **Measured** (October 7, CDF 5/3, 207 Hz, 2080x2208, 10-12 s ABBA cells): two connections cut
  ALVR's network-stage estimate by about 0.7 ms at 1000 Mbit/s and 1.8 ms at 1500. Fresh FPS did
  not change at 1000 Mbit/s, because headset decode was the limit. Details:
  [BITRATE.md](BITRATE.md#parallel-wired-video-connections-october-7).

**Not checked yet:**

- Unplugging and replugging the USB cable mid-stream with two wired connections. If video stops
  after a replug, stop SteamVR, confirm `adb devices` lists the headset, then set
  **Wired video connections** to **0** and restart.
- The `.64` parallel wired path on hardware. `.64` was checked over Wi-Fi 6E only, because USB adb
  was offline; alpha.9 (`.63`) wired streaming was last measured on `.62`.

## Verify that video uses USB

Check `adb forward --list` for ports **9943** (control) and **9944** (stream), plus
**9950-9953** for the parallel video connections, and verify the server's active peer is
**127.0.0.1**. The server log line `[PYROWAVE] wired video over 2 parallel connections` shows they
engaged; `video stays on the stream socket` shows the fallback. A cable being plugged in does not
prove video uses USB. An ADB-over-Wi-Fi connection is also not USB evidence; use `adb devices -l`
to confirm a physical USB device. Disconnect or disable wired mode to return to Wi-Fi.

## USB-only helpers

The PC changes headset properties through adb over USB only:

- **Quest 3: set the panel refresh rate over USB.** With a rate above 120 Hz selected, the PC
  sets the panel to that whole rate and restarts the client: up to 207 Hz in the native panel
  mode, above 207 Hz up to 240 Hz in the scaled mode (1552x1664 per eye). The 240 Hz profile and
  the 165/180/200/240 Hz refresh presets turn it on; for other whole rates, set
  **Video → Preferred FPS** and turn the option on yourself.
- **Quest 3: maximum GPU clock** (690 MHz instead of about 640). The 207 Hz and 240 Hz
  "(measured)" profiles turn it on.

The PC saves the headset's previous values first and puts them back when the request is
withdrawn (120 Hz or less selected, or the option turned off) or SteamVR closes. Over Wi-Fi
neither runs. See [BUILD.md](BUILD.md#install-and-rollback) and
[REFRESH-RATES.md](REFRESH-RATES.md).

## Throughput

USB's advertised link rate is not ADB payload throughput. A USB 2 cable/port, ADB copies,
CPU load and TCP buffering can limit it. On the owner's PC, `adb forward` carried 3.5-3.65 Gbit/s
of continuous data, but frame bursts crossed at about half that. At 207 Hz with 2080x2208 per eye,
Haar and the maximum GPU clock, 1000 Mbit/s gave 194-197 fresh FPS over USB in 10-12 s screens;
2000 Mbit/s lost frames to the link (167-170 fresh FPS). 1000-1500 Mbit/s is the useful
range at 207 Hz; measure your own cable and port. USB removes the Wi-Fi hop; it does not remove
Quest GPU wavelet decode, hardware-buffer conversion, compositor work or their thermal limits.
Match resolution, refresh, chroma and scene when comparing USB and wireless latency.
