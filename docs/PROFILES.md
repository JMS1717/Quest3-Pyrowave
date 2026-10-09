# Streaming profiles

A profile sets every stream setting at once: refresh rate, stream size, wavelet, bitrate, chroma,
downsample filter, sharpening, transport and the GPU clock. Pick one, then change single values only
if you have a reason to. The same profiles are in the dashboard and in the headset.

## Which one should I pick?

| Your setup | Pick | Why |
| --- | --- | --- |
| **USB cable** (recommended) | **Quality 120 Hz** | The best balance: sharp, smooth and low latency |
| USB, slow games, sims, racing | **Godlike 90 Hz** | The cleanest full-size image, a steady 90 Hz |
| USB, the most detail at 120 Hz | **Godlike 120 Hz** | Full 3072 × 3216 per eye, a few FPS below 120; faint blocks in gradients |
| USB, menus, cockpits, coloured text | **Colour 4:4:4 120 Hz** | Full colour detail at 120 Hz |
| USB, fast shooters, lowest latency | **Competitive 207 Hz** | 207 Hz, if Horizon OS allows it (see below) |
| **Wi-Fi 6E** (6 GHz, PC on Ethernet) | **Wi-Fi Quality 120 Hz** | 120 Hz at 1000 Mbps |
| Ordinary Wi-Fi 6 (5 GHz) | **Wi-Fi 90 Hz** | 90 Hz at 700 Mbps, steadier on weaker links |
| First start, or nothing else streams | **Starter 72 Hz** | The fresh-install settings; works on any connection |

**Best overall:** Quality 120 Hz over USB. In every profile, set the dashboard's
**Game render resolution** to 150 % or more if your PC holds the frame rate: the game then renders
more pixels than the stream carries, and the PC downsamples them with a sharp filter.

## Changing profiles

**In the headset.** Hold **both thumbsticks** for about 0.7 s. The settings menu opens with
**Profile** on the first row. Move left or right to choose a profile; the box under the rows explains
the one you are on. Move to **Apply** and press **A**. The PC saves the profile and the stream
reconnects; when the wavelet or refresh rate changes, SteamVR restarts and video returns in up to
25 s, so save your game first. **B** or **Y** closes the menu without applying.

USB-only profiles are refused on Wi-Fi, with a message saying so, and a profile whose refresh rate
the headset did not offer is refused too. Changing a single value below the Profile row (refresh,
bitrate, stream size, render size, chroma) leaves the profile as **Custom**. Choosing a profile again
resets all values to it.

**On the PC.** Open the dashboard, **Settings → Presets → Streaming profile**, and pick one. Each
entry shows its description. Changing any setting afterwards leaves no profile selected; pick it
again to return to it.

## The profiles

Fresh FPS counts distinct decoded frames shown per second, not the refresh rate the headset
reports. The measurements are short live screens on one Quest 3 over USB with a PC that holds the
frame rate; a heavy game, a slower PC or a weaker link gives less. ALVR's latency figure is its
pipeline estimate, not an optical motion-to-photon measurement.

### Quality 120 Hz (recommended for USB)

| | |
| --- | --- |
| Refresh | 120 Hz |
| Stream per eye | 110 % of the panel: 2272 × 2432 |
| Codec | PyroWave CDF 5/3, 4:2:0, 1500 Mbps |
| PC | Lanczos downsample, sharpening 30 |
| Headset | maximum GPU clock (applied over USB) |
| Measured | 111–120 fresh FPS of 120 |

A little more than the panel's own resolution: the lens magnifies the centre of the image, so the
extra 10 % shows there. 110 % is the largest stream that held 120 Hz in a heavy SteamVR Home scene.
CDF 5/3 has no block edges in gradients or motion.

### Godlike 120 Hz

| | |
| --- | --- |
| Refresh | 120 Hz |
| Stream per eye | 3072 × 3216, the PC's full render size with no downsample (Virtual Desktop's Godlike size) |
| Codec | PyroWave **Haar**, 4:2:0, 1500 Mbps |
| Headset | maximum GPU clock |
| Measured | 116–118 fresh FPS of 120; ALVR latency estimate about 43 ms |

The most detail at 120 Hz. Each frame carries nearly twice the pixels of Quality 120 at the same
bitrate, and decoding takes most of a 120 Hz frame. It uses Haar, the fastest wavelet: CDF 5/3
measured 112 fresh FPS here against Haar's 116-117, and added about 5 ms to ALVR's latency
estimate because frames waited for the decoder. Haar's cost is faint 8-32 pixel blocks in smooth
gradients and fast motion; if you see them, use Godlike 90 or Quality 120.

### Godlike 90 Hz

| | |
| --- | --- |
| Refresh | 90 Hz |
| Stream per eye | 3072 × 3216 |
| Codec | PyroWave CDF 5/3, 4:2:0, 1500 Mbps: a third more bits per frame than Godlike 120 |
| Headset | maximum GPU clock |
| Measured | 89–90 fresh FPS of 90; ALVR latency estimate 48–54 ms |

The cleanest full-size image: CDF 5/3 has no block edges, and 90 Hz gives each frame a third more
bits and decode time. Best for slower games, simulators, racing and flight, where detail matters
more than the last bit of motion smoothness.

### Colour 4:4:4 120 Hz

| | |
| --- | --- |
| Refresh | 120 Hz |
| Stream per eye | 110 %: 2272 × 2432 |
| Codec | PyroWave Haar, **4:4:4**, 1500 Mbps |
| Headset | maximum GPU clock |
| Measured | 118–120 fresh FPS of 120 |

Every other profile sends colour at a quarter of the resolution (4:2:0), which is invisible in
most scenes but softens thin red and blue lines and coloured text. 4:4:4 keeps them crisp. It uses
the faster Haar wavelet to fit the extra colour data into a 120 Hz frame, so gradients can show
faint 8-pixel blocks that the CDF 5/3 profiles do not.

### Wi-Fi Quality 120 Hz

| | |
| --- | --- |
| Refresh | 120 Hz |
| Stream per eye | 110 %: 2272 × 2432 |
| Codec | PyroWave CDF 5/3, 4:2:0, 1000 Mbps over UDP |
| Network | Wi-Fi 6E: a 6 GHz access point near the headset, the PC on Ethernet |
| Measured | 1000 Mbps is the measured sustainable rate on Wi-Fi 6E ([Wi-Fi](WIRELESS.md)); this profile's 120 Hz screen is pending |

The maximum GPU clock and the panel helpers need a USB cable, so this profile leaves them off. If
the image stutters or lags more and more, the link is not carrying 1000 Mbps: pick Wi-Fi 90.

### Wi-Fi 90 Hz

| | |
| --- | --- |
| Refresh | 90 Hz |
| Stream per eye | the panel's own size: 2064 × 2208 |
| Codec | PyroWave CDF 5/3, 4:2:0, 700 Mbps over UDP |
| Network | Wi-Fi 6 (5 GHz) or 6E |
| Measured | not yet measured live |

About the same bits per frame as Wi-Fi Quality, on a link that only carries 700 Mbps.

### Competitive 207 Hz

| | |
| --- | --- |
| Refresh | 207 Hz (the PC switches the panel over USB) |
| Stream per eye | 2080 × 2208 |
| Codec | PyroWave CDF 5/3, 4:2:0, 1000 Mbps |
| PC | adaptive downsample, sharpening 60 |
| Headset | maximum GPU clock |
| Measured | 194–197 fresh FPS with Haar before Horizon OS build 209; 5/3 costs a few FPS |

The lowest latency and the smoothest motion, at less detail per frame. **Horizon OS build 209
(v2.9, October 2026) holds the headset at 120 Hz or less**, so on that build this profile cannot
reach 207 Hz; use Quality 120 until Meta fixes it. [High refresh →](HIGH-REFRESH.md)

### Starter 72 Hz

| | |
| --- | --- |
| Refresh | 72 Hz |
| Stream per eye | the panel's own size |
| Codec | PyroWave CDF 9/7, 4:2:0, 400 Mbps |
| Decode | Auto |

The fresh-install settings. They ask little of the link and the PC, so they show whether the
stream works at all. Move to Quality 120 (USB) or Wi-Fi Quality 120 once it streams.

### Reference and comparison entries

The dashboard also lists three **(measured)** profiles, which reproduce the October 6–7
measurements in [High refresh](HIGH-REFRESH.md), and **H.264**, **HEVC** and **AV1** comparisons
that use the headset's hardware video decoder at 200 Mbps. They are for comparisons, not play, and
the headset menu does not offer them.

## What each setting does

| Setting | What it changes | Better is… |
| --- | --- | --- |
| **Refresh rate** | Frames per second the headset shows. | Higher is smoother with lower latency, but each frame gets fewer bits and less decode time. 120 Hz is the balance. |
| **Bitrate** | Megabits per second of video. | More means fewer artifacts, mostly in motion. USB carries about 2000 Mbps, Wi-Fi 6E about 1000. Changes apply without a reconnect. |
| **Stream resolution** | Pixels encoded per eye, as a percentage of the panel. | Above 100 % shows more detail in the lens centre; 110 % is the largest that holds 120 Hz. |
| **Game render resolution** | Pixels the game renders per eye. | 150 % or more is sharpest; it costs only PC GPU time. |
| **Wavelet** | PyroWave's transform. | **CDF 5/3**: smooth gradients, no block edges. **Haar**: fastest decode, faint 8-pixel blocks. **CDF 9/7**: smoothest, slowest decode. |
| **Chroma** | Colour resolution. | **4:2:0** decodes faster; **4:4:4** keeps coloured detail crisp. |
| **Downsample filter** | How the PC shrinks the game's render to the stream size. | **Lanczos** is sharpest; **Adaptive** is used at 207 Hz. |
| **Sharpening** | Sharpening on the PC before encoding. | 30 for 120 Hz, 60 at 207 Hz. More looks crisper but can halo edges. |
| **Maximum GPU clock** | Holds the Quest's GPU at 690 MHz while streaming over USB. | On for USB profiles: it shortens decoding. |

More detail: [bitrate](BITRATE.md) · [Decoder V2 and CDF 5/3](DECODER-V2.md) ·
[chroma](CHROMA.md) · [render and stream sizes](RENDER-ENCODE-RESOLUTION.md) ·
[sharpening](SHARPENING.md) · [settings that restart SteamVR](SETTINGS-APPLY.md)
