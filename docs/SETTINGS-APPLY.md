# Applying stream-start settings

Status: on `claude/decoder-v2` from 89caa8c, checked in the headset on 2026-10-07.

## Problem

Upstream ALVR reads most video and driver settings once per connection: the SteamVR driver
configuration (`OpenvrConfig`: codec, PyroWave wavelet, chroma, transport, foveation, downsample
filter, controllers, colour correction, encoder options) and the negotiation inputs (refresh,
stream and render resolution). The dashboard marks them "Changing this setting will make SteamVR
restart!", but an edit made while streaming was only saved. It took effect after the headset next
reconnected, which during a session meant never, so 4:4:4, wavelet and foveation changes appeared
to do nothing.

## Fix

The streamer's control loop (`connection.rs`) compares those settings with their values at stream
start every 0.5 s. Once a change has been stable for 2 s (a slider drag reconnects once), it logs
`[Q3PW_SETTINGS_RECONNECT] Reconnecting to apply <fields>` and sends the client `Restarting`, as the
headset settings menu already did. The client reconnects; the new connection rebuilds the driver
configuration and restarts SteamVR when it changed. Settings that only the negotiation reads
reconnect without a SteamVR restart. Live settings (bitrate) are not compared.

## Checked in the headset

Owner session (207 Hz, 2080x2208 per eye, Haar 4:2:0, 700 Mbps, wired), client left running,
setting changed through the dashboard API while streaming:

| change | reconnect | new SteamVR | streaming again | driver filter |
|---|---|---|---|---|
| downsample Adaptive → Lanczos | 2.4 s | 21.5 s | 26.5 s | 1 → 2 |
| bitrate 700 → 800 Mbps | none | none | no gap over 1 s | — |
| bitrate 800 → 700 Mbps | none | none | no gap over 1 s | — |
| downsample Lanczos → Adaptive | 2.4 s | 21.4 s | 26.0 s | 2 → 1 |

Most of the 26 s is SteamVR shutting down and starting again.
