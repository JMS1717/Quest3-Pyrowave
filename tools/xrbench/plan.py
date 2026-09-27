"""The benchmark sweep: which configurations run, in what order, and sanity checks on them."""
import json
from dataclasses import asdict, dataclass, replace

from alvr_ffe_calc import FoveationConfig, fits_limit

from . import paths

NVENC_H264_LIMIT = (4096, 2048)      # side-by-side encode size
NVENC_H264_MAX_MBPS = 1200           # ALVR crashes above this (guide, 20.13)
SWITCH_TO_VD_MINUTES = 4.0           # SteamVR/driver swap + VD connect
SETUP_MINUTES = 3.0


@dataclass(frozen=True)
class Segment:
    label: str
    stack: str                       # "alvr" or "vd"
    codec: str = "H264"
    mbps: int = 400
    eye: int = 2560
    hz: int = 72
    foveation: bool = True
    buffering: float = 2.0
    # ALVR's own default is true (settings.rs enforce_server_frame_pacing). With it set the server
    # sleeps until the next vsync before producing a frame -- up to a frame period of deliberate
    # delay -- and without it yields instead. Default matches ALVR so older plans stay comparable.
    frame_pacing: bool = True
    # Microseconds to release SteamVR *before* the virtual vsync rather than exactly on it, so the
    # frame is produced, encoded and sent that much earlier. 0 is today's behaviour. Read by the
    # patched server from ALVR_PACING_HEADROOM_US, so it is an env var rather than a session
    # setting and must be set before SteamVR starts.
    pacing_headroom_us: int = 0
    # Release SteamVR this many microseconds *after* the virtual vsync. The opposite of
    # pacing_headroom_us, and the only control measured so far that can move input_acquired
    # later, which is the one thing that shortens predicted_display_time - input_acquired
    # rather than rearranging the stages under it.
    pacing_delay_us: int = 0
    # Phase-lock the server's virtual vsync to the headset's display (patched server,
    # ALVR_PHASE_LOCK=1, read once at driver start so it is an env var set before SteamVR).
    # Measured: a fixed start delay is absorbed entirely by the client waiting; the
    # controller steers that waiting toward a small target instead.
    phase_lock: bool = False
    # Client polls for the decoded frame before xrWaitFrame (patched client, device property
    # debug.xrwired.early_poll, read once at client start). Default matches the patched client's
    # default so plans that do not mention it measure the same thing as before.
    early_poll: bool = True
    # PyroWave reconstruction path on the headset: "fragment" | "compute" | "auto" (the driver
    # heuristic, fragment on Qualcomm). Device property debug.xrwired.decode_path, read by
    # libpyroclient once at create. Experiment 1.
    decode_path: str = "auto"
    # Experiment 2: PyroWave wavelet, "97" (CDF 9/7) or "53" (CDF 5/3, compute path
    # only). Server env ALVR_PYROWAVE_WAVELET; the client learns it from the decoder config blob.
    wavelet: str = "97"
    # PyroWave's own transport (video.pyrowave.transport): every PyroWave plan measured so far
    # streamed over UDP. Independent of `protocol`, ALVR's stream socket.
    pyro_transport: str = "Udp"
    # PyroWave math/storage precision on the headset: 0 = FP16 math and all-R16F storage, 1 = FP32
    # math with two R16F levels (the library default), 2 = FP32; -1 leaves the library's default.
    # Device property debug.xrwired.pyro_precision, read by libpyroclient once at create.
    pyro_precision: int = -1
    # Experiment 4 corpus: dump `dump_frames` consecutive lossless encoder-input frames (one
    # multi-frame y4m, ALVR_PYROWAVE_DUMP_COUNT) starting at encoder frame `dump_start`, filed
    # under `dump_label` (a content class). 0 = no dump.
    dump_frames: int = 0
    dump_start: int = 120
    dump_label: str = ""
    # SGSR (Snapdragon Game Super Resolution) -- edge-directed sharpening plus upscale, already
    # implemented in graphics/resources/stream.wgsl and running in the same fragment invocation as
    # the foveation inverse warp, so it adds no pass. ALVR ships it disabled. It costs client GPU
    # only: `upscale_factor` scales the swapchain, so the composite pass runs factor^2 more
    # fragments while the encoded frame -- and therefore decode -- is untouched.
    upscaling: bool = False
    upscale_factor: float = 1.5
    edge_sharpness: float = 2.0
    nvenc_preset: int = 1
    protocol: str = "Tcp"
    transport: str = "wifi"          # "wifi", "rndis" (USB tethering network) or "adb" (USB adb forward)
    packet_size: int = 1400          # ALVR connection.packet_size (bytes per shard; 1400 suits UDP only)
    decoder: str = ""                # "" = ALVR default (by MIME type); else a MediaCodec name
    low_latency: bool = False        # Android KEY_LOW_LATENCY on the decoder
    scene_seconds: int = 33          # fits the Galaxy XR eye-monitor window (see seconds_awake_needed)
    photo: str = ""                  # path (on the PC) to a 1200x640 mosaic: scene_app's photo layout
    game_seconds: int = 0            # load phase off: no VR game installed, SteamVR void is no load
    # "synthetic" = scene_app for scene_seconds (the default, every earlier plan); "home" =
    # static SteamVR Home measured between logcat markers (sweep.run_home_with_captures).
    scene: str = "synthetic"
    # Home cells only: dump this many lossless encoder-input frames inside the measure window
    # (runtime trigger) so xrbench.quality can score the tapped stream against them. 0 = none.
    quality_frames: int = 0
    # Beta validation: clear every research override (headset properties, server env vars) so the
    # session settings alone configure the stream, as they do for a tester (sweep.research_overrides).
    settings_only: bool = False
    note: str = ""

    # --- workload class and FFE shape (2e) ---
    # Defaults reproduce the harness's pre-2e behaviour exactly, so existing plans and plan JSONs
    # keep working untouched.
    workload: str = "control"        # control | full | seated | seated_quality | screen
    eye_h: int = 0                   # 0 = square (use `eye`); else the per-eye render height
    center_size_x: float = 0.45      # FFE full-resolution centre, fraction of the frame
    center_size_y: float = 0.40
    edge_ratio_x: float = 3.0        # peripheral compression ratio
    edge_ratio_y: float = 4.0
    gaze: bool = True                # False sets ALVR_GAZE_FOVEATION=0: static centre, the 2e A/B
    render_scale: float = 1.0        # SteamVR supersampling; raises *game* render, not encoded size

    # False means the segment is valid with the headset merely awake and the wear sensor covered.
    # Encoded resolution derives from center_size and edge_ratio alone, never from the gaze-driven
    # shift (see CalculateFoveationVars), so fps, decode time, bitrate, packet loss and thermals
    # are all unaffected by a frozen gaze. Only gaze *behaviour* -- clamp rate, staleness, shift
    # range -- and anything judged by eye need a wearer.
    requires_wearer: bool = False

    def minutes(self):
        # measured on the PC: SteamVR restart + reconnect ~25 s; cool-down only
        # runs when the headset is warm, so it is not budgeted here
        restart, connect, settle, wrap_up = 50, 12, 5, 15   # two SteamVR restarts: apply config, stream
        game = self.game_seconds + (15 if self.game_seconds else 0)
        return (restart + connect + settle + self.scene_seconds + game + wrap_up) / 60.0

    def eye_size(self):
        """Per-eye render size. `eye_h` of 0 means square, which is what every pre-2e plan assumed.

        Square is not actually right: the panel is 3552x3840 (aspect 0.925) and the FOV is
        94.4x105.1 deg (aspect 0.898), so a square render matches neither and under-samples
        vertically. New plans should set `eye_h`."""
        return self.eye, (self.eye_h or self.eye)

    def foveation_config(self):
        """This segment's FFE geometry, or None when foveation is off."""
        if not self.foveation:
            return None
        return FoveationConfig(center_size_x=self.center_size_x, center_size_y=self.center_size_y,
                               edge_ratio_x=self.edge_ratio_x, edge_ratio_y=self.edge_ratio_y)

    def configure_args(self):
        width, height = self.eye_size()
        # The beta build reads PyroWave from the session; the research env vars and the headset
        # decode-path property still override it where a plan sets them.
        return ["-Codec", self.codec, "-Mbps", str(self.mbps), "-EyeSize", str(width),
                "-EyeHeight", str(height),
                "-RefreshHz", str(self.hz), "-MaxBufferingFrames", str(float(self.buffering)),
                "-FramePacing", "on" if self.frame_pacing else "off",
                "-Upscaling", "on" if self.upscaling else "off",
                "-UpscaleFactor", str(float(self.upscale_factor)),
                "-EdgeSharpness", str(float(self.edge_sharpness)),
                "-NvencPreset", str(self.nvenc_preset), "-Protocol", self.protocol,
                "-Foveation", "on" if self.foveation else "off",
                "-FoveationCenterX", str(self.center_size_x),
                "-FoveationCenterY", str(self.center_size_y),
                "-FoveationEdgeX", str(self.edge_ratio_x),
                "-FoveationEdgeY", str(self.edge_ratio_y),
                "-PacketSize", str(self.packet_size),
                "-PyroTransport", self.pyro_transport, "-Wavelet", self.wavelet,
                "-DecodePath", self.decode_path, "-FollowGaze", "on" if self.gaze else "off"] + \
               (["-DecoderName", self.decoder] if self.decoder else []) + \
               (["-LowLatency", "1"] if self.low_latency else [])


AWAKE_CONNECT_S = 20   # eye-timer reset -> client streaming + 4 s liveness check
AWAKE_SETTLE_S = 2


def seconds_awake_needed(segment):
    """Seconds from the eye-timer reset (sleep+wake) to the last capture. With the wear sensor
    covered, Galaxy XR sleeps 90 s after it stops seeing eyes and shows a warning at 60 s."""
    return AWAKE_CONNECT_S + AWAKE_SETTLE_S + segment.scene_seconds


def default_plan():
    """H.264 + foveation bitrate ladder at 2560/eye over Wi-Fi, then USB RNDIS. 800+ Mbps queued
    15 s of video on both links, so the ladder brackets each link's real ceiling.
    HEVC dropped: its decoder caps near 500 Mbps and overheats the headset."""
    segments = []
    for prefix, transport in (("W", "wifi"), ("U", "rndis")):
        for i, mbps in enumerate((400, 500, 600, 800), 1):
            segments.append(Segment(f"{prefix}{i}-{mbps}", "alvr", mbps=mbps, transport=transport,
                                    note=f"{transport} {mbps} Mbps"))
    return segments


def adb_plan():
    """The same ladder over the USB adb tunnel (adb forward 9943/9944, ALVR at 127.0.0.1). adb moves
    bulk USB transfers and delivered ~1 Gbps, versus ~0.44 Gbps for RNDIS."""
    return [Segment(f"A{i}-{mbps}", "alvr", mbps=mbps, transport="adb", note=f"adb {mbps} Mbps")
            for i, mbps in enumerate((400, 500, 600, 800), 1)]


def packet_plan():
    """ALVR shards every frame into packet_size writes; over the adb tunnel each write is an adb
    message, so 1400-byte shards may be what caps ALVR near 500 Mbps on a ~1.17 Gbps link."""
    seg = lambda label, transport, mbps, packet: Segment(label, "alvr", mbps=mbps, transport=transport,
                                                         packet_size=packet, note=f"{transport} {mbps} Mbps, {packet} B packets")
    return [seg("W2-500r", "wifi", 500, 1400), seg("W4-800p64", "wifi", 800, 65000),
            seg("A3-600p64", "adb", 600, 65000), seg("A4-800p16", "adb", 800, 16384),
            seg("A4-800p64", "adb", 800, 65000)]


def decoder_plan():
    """Is ALVR's ~15 ms "decode" a one-frame hold? Same Wi-Fi 400/800 runs with the low-latency
    decoder component and Android's KEY_LOW_LATENCY (M0 in the feasibility study)."""
    ll = "c2.qti.avc.decoder.low_latency"
    seg = lambda label, mbps, **kw: Segment(label, "alvr", mbps=mbps, transport="wifi", **kw)
    return [seg("D1-400base", 400), seg("D2-400ll", 400, decoder=ll, low_latency=True),
            seg("D3-400key", 400, low_latency=True),
            seg("D4-800base", 800), seg("D5-800ll", 800, decoder=ll, low_latency=True)]


QTI_LOW_LATENCY_DECODER = {                   # present on the Galaxy XR (checked on the headset)
    "H264": "c2.qti.avc.decoder.low_latency",
    "Hevc": "c2.qti.hevc.decoder.low_latency",
}

UNFOVEATED_LADDER = (800, 600, 500, 400, 300, 250, 200)
UNFOVEATED_TRACKS = (("Hevc", 2560, "H26"), ("Hevc", 2048, "H20"), ("H264", 2048, "A20"))


def unfoveated_plan():
    """Wi-Fi with foveation off, descending the bitrate ladder from the top.

    Foveation off is what makes this measurable: FFE resamples the frame before the encoder sees
    it, so any spatial-frequency comparison is really a comparison of FFE. It also fixes each
    codec's reach -- 2048/eye is H.264's unfoveated ceiling, since side-by-side makes that
    4096x2048 and NVENC's 4096 width binds first. HEVC at 2048 is therefore the control cell:
    without it, H26 vs A20 varies codec and encoded resolution together and neither can be
    blamed for a difference.

    The ladder descends because the top is the unknown -- over Wi-Fi 800 Mbps arrives (786
    measured, versus 375 over RNDIS, which caps at 445), so the ceiling here is the
    headset decoder, not the link. Each step runs all three tracks before dropping, so codecs are
    compared under the same thermal state rather than at opposite ends of the session.

    Every segment names its codec's Qualcomm low-latency decoder: that took total
    pipeline latency from 93.1 ms to 74.5 ms at 400 Mbps, while Android's KEY_LOW_LATENCY alone
    only reached 89.1 ms. It is a settled win, so it is held fixed rather than re-measured."""
    segments = []
    for mbps in UNFOVEATED_LADDER:
        for codec, eye, prefix in UNFOVEATED_TRACKS:
            segments.append(Segment(
                f"{prefix}-{mbps}", "alvr", codec=codec, mbps=mbps, eye=eye, transport="wifi",
                foveation=False, decoder=QTI_LOW_LATENCY_DECODER[codec], low_latency=True,
                note=f"{codec} {eye}/eye unfoveated, {mbps} Mbps Wi-Fi"))
    return segments


def foveated_plan():
    """The same descent with foveation on, both codecs at 2560/eye over Wi-Fi.

    The unfoveated sweep found the headset's HEVC decoder is what decides the codec
    question: at 2048/eye and 800 Mbps, H.264 decoded in 11.6 ms and held 72 fps while HEVC took
    97.5 ms and halved to 36 fps. HEVC only held 72 fps at or below ~400 Mbps at 2048/eye, and
    ~300 at 2560/eye.

    Foveated encoding is the obvious thing that could change that: it shrinks the frame before the
    encoder, so there is less for the decoder to chew on. It also lets H.264 reach 2560/eye at all
    -- unfoveated that is 5120x2560, past NVENC's 4096 width -- so for the first time both codecs
    run at the same encoded resolution and the comparison isolates the codec.

    Whether the detail FFE discards costs more than the frames it saves is the open question, and
    the stills answer it alongside the latency."""
    segments = []
    for mbps in UNFOVEATED_LADDER:
        for codec, prefix in (("Hevc", "FH"), ("H264", "FA")):
            segments.append(Segment(
                f"{prefix}-{mbps}", "alvr", codec=codec, mbps=mbps, eye=2560, transport="wifi",
                foveation=True, decoder=QTI_LOW_LATENCY_DECODER[codec], low_latency=True,
                note=f"{codec} 2560/eye foveated, {mbps} Mbps Wi-Fi"))
    return segments


def vd_plan():
    return [
        Segment("V01-VD", "vd", codec="preset", mbps=0, note="Virtual Desktop, owner preset"),
        Segment("V02-VD", "vd", codec="preset", mbps=0, note="Virtual Desktop repeat"),
    ]


# --- 2e: workload classes and quality presets ---
#
# Geometry is not hand-picked. Each row is the largest FOV-aspect render that keeps the encoded
# size inside the decode budget measured on this headset. `center_size_y` is
# `center_size_x * 0.40/0.45`, preserving ALVR's default centre aspect.
#
# One operating point: 72 Hz. The brief -- "lets keep 90 off the benchmarks for now only
# 72 from here on out". The 60 Hz rows were sized for a frame period ~20% longer, so each pays for
# 72 Hz by giving up about four points of `center_size_x` -- a slightly smaller sharp region at
# the same render resolution, which is the lever gaze tracking exists to make affordable. Their
# px/deg, the headline number of every tier, is unchanged.
#
# The budget is 2.45 Mpx/eye, measured rather than extrapolated: xrbench-runs/refresh2 ran panel
# native at centre 0.20 -- 1664x1472 = 2.45 Mpx -- at 71.98 fps, 15.88 ms decode, 0 packets lost.
# Q-SCREEN-BEST already encodes exactly that, so it moved to 72 Hz untouched.
#
# Columns: label, workload, hz, center_size_x, render width, render height, Mbps.
BENCH_HZ = 72

# The brief: "ideally we're throwing out anything under 60 milliseconds latency ... If
# it's over 60, mark it as fail." This is motion-to-photon as ALVR reports it
# (`total_pipeline_latency_s`), not any one stage.
#
# Nothing measured to date clears it -- the resolution sweep ran 84 to 103 ms -- and decode plus
# network is only about a quarter of that. Roughly 73 ms sits in encode, queuing, compositor and
# vsync, so the bar cannot be reached by changing geometry, codec or transport. `buffering`
# defaults to 2.0 frames, 27.8 ms of deliberate queue at 72 Hz, and `enforce_server_frame_pacing`
# sleeps until the next vsync for up to another frame period. Those two are the levers.
LATENCY_BUDGET_MS = 60.0


def latency_verdict(total_ms):
    """"pass"/"fail" against the latency bar, or None when a segment produced no telemetry.

    None rather than "fail" on purpose: a capture that did not report is an unknown, and recording
    it as a failure would quietly turn a broken harness into a result about the configuration."""
    if total_ms is None:
        return None
    return "pass" if total_ms <= LATENCY_BUDGET_MS else "fail"


# The largest encoded frame this headset is known to sustain at 72 Hz. Measured by
# `resolution_plan` (xrbench-runs/resolution1), which swept the sharp region at panel
# native and settled what had previously been guessed:
#
#   centre  encoded      Mpx   decode   fps    fps min   lost
#     0.15  1536x1344   2.06   14.85   71.96     35.99      0
#     0.20  1664x1472   2.45   15.81   71.98     36.00      0
#     0.25  1792x1600   2.87   18.68   71.98     36.00      0   <- budget
#     0.30  1920x1760   3.38   18.66   71.92     14.40      0
#     0.35  2016x1856   3.74   18.88   71.96     24.00      0
#
# **Decode is not pixel-bound here.** An 82% increase in encoded pixels cost 27% more decode, and
# from 2.87 Mpx upward it is flat within noise -- 18.68, 18.66, 18.88. The previous
# "~6.5 ms/Mpx" note in this file was a two-point extrapolation and was wrong; every cell held
# ~72 fps with zero packets lost, including 3.74 Mpx, 53% above the old budget.
#
# The budget sits at 2.87 Mpx rather than 3.74 because *mean* frame rate is not the whole story:
# fps_min stays at the 36.0 baseline through 0.25 and degrades above it (14.40 at 0.30, 24.00 at
# 0.35). Mean fps held everywhere, so 3.74 Mpx is usable if a deeper occasional dip is acceptable;
# 2.87 is where nothing gets worse at all.
DECODE_BUDGET_PX = 1792 * 1600
QUALITY_LADDER = (
    # Today's proven config, square render included: the control, re-run first every sitting.
    ("Q-CONTROL",       "control",        72, 0.45, 2560,    0, 400),

    # 1 VR Full Motion -- room-scale, 72 Hz, latency and motion stability first.
    ("Q-FULL-GOOD",     "full",           72, 0.40, 2560, 2848, 400),
    ("Q-FULL-BETTER",   "full",           72, 0.35, 2688, 3008, 600),
    ("Q-FULL-BEST",     "full",           72, 0.30, 2880, 3200, 800),

    # 2 VR Seated -- controller/UEVR, moderate head motion, 72 Hz, balanced.
    ("Q-SEAT-GOOD",     "seated",         72, 0.35, 2688, 3008, 400),
    ("Q-SEAT-BETTER",   "seated",         72, 0.30, 2880, 3200, 600),
    ("Q-SEAT-BEST",     "seated",         72, 0.20, 3328, 3712, 800),

    # 3 VR Seated Quality -- limited head motion, the highest px/deg of the seated classes.
    ("Q-SQ-GOOD",       "seated_quality", 72, 0.31, 2944, 3264, 400),
    ("Q-SQ-BETTER",     "seated_quality", 72, 0.26, 3136, 3488, 600),
    ("Q-SQ-BEST",       "seated_quality", 72, 0.21, 3392, 3776, 800),

    # 4 3D Giant Screen -- ~70 deg virtual screen at ~10 ft, head near-still, eyes roaming.
    # Tighter centres than seated throughout: this mode trades everything for spatial detail,
    # and the sharp region follows the eye across a screen of known extent.
    # Q-SCREEN-BEST reaches 3520x3840 = 99% of panel native (37.3 vs 37.6 px/deg).
    ("Q-SCREEN-GOOD",   "screen",         72, 0.26, 3136, 3488, 400),
    ("Q-SCREEN-BETTER", "screen",         72, 0.21, 3392, 3776, 600),
    ("Q-SCREEN-BEST",   "screen",         72, 0.20, 3520, 3840, 800),
)

# The gaze A/B. Only the top of each head-still ladder: those are the tight centres where a static
# centre should hurt most, so they are where gaze tracking has to prove itself.
GAZE_OFF_ARMS = ("Q-SQ-BEST", "Q-SCREEN-BEST")


def quality_plan():
    """Workload-class presets, plus a gaze-off arm for the tightest centre in each 60 Hz class."""
    segments = []
    for label, workload, hz, center_x, width, height, mbps in QUALITY_LADDER:
        # The control keeps ALVR's 1400 default: it exists to reproduce today's proven config
        # exactly, so changing anything about it -- including a setting we believe is an
        # improvement -- destroys the only fixed reference the ladder has.
        shard = 1400 if workload == "control" else TCP_SHARD_BYTES
        # 1.5 frames, measured. The buffering sweep (xrbench-runs/buffering1) found it
        # better than ALVR's 2.0 default on every axis at once -- 89.26 ms total against 104.53,
        # and fps_min 71.58 against 36.0 -- which is unusual, since shallowing a buffer normally
        # trades smoothness for latency. Lower is worse, not better: 1.0 gave 98.44 ms and an
        # fps_min of 4.8, because the wait relocates into vsync_queue and frames begin missing
        # their deadline. The control keeps 2.0 for the same reason it keeps 1400-byte shards.
        buffering = 2.0 if workload == "control" else 1.5
        segments.append(Segment(
            label, "alvr", codec="H264", mbps=mbps, eye=width, eye_h=height, hz=hz,
            workload=workload, center_size_x=center_x, packet_size=shard, buffering=buffering,
            center_size_y=round(center_x * 0.40 / 0.45, 3),
            note=("today's proven config, control" if workload == "control"
                  else f"{workload} {label.rsplit('-', 1)[-1].lower()}")))

    by_label = {s.label: s for s in segments}
    for label in GAZE_OFF_ARMS:
        base = by_label[label]
        # identical but for the gaze flag -- anything else and it is not an A/B
        # only meaningful against a *moving* gaze: with the headset off, both arms are identical
        segments.append(replace(base, label=f"{label}-NG", gaze=False, requires_wearer=True,
                                note=f"{base.workload} gaze off (A/B)"))
    return segments


PANEL_PER_EYE = (3552, 3840)     # measured via dumpsys display; 7104x3840 total

# ALVR's 1400 default is an MTU-sized value that suits UDP. These runs are TCP, where the kernel
# segments anyway, so 1400-byte writes are ~53,000 syscalls a second of pure overhead -- landing on
# the CPU that measures 6 C hotter than the decode block and is what actually throttles.
# Measured across four runs at identical geometry and bitrate, 1400 -> 65000:
#   network   ~16-18 -> ~13 ms   (consistent)
#   decode    ~18.6  -> ~17.1 ms (consistent)
#   fps min   6-30   -> 30-60    (better worst case, not drop-free)
# NOT a thermal fix: a controlled rerun showed both shard sizes converge on the same ~73-74 C
# steady state, and the first run's apparent "zero thermal rise" was only that cell starting hot.
# Left off `default_plan` and the other historical plans on purpose, so the 45-segment baseline
# table stays comparable.
TCP_SHARD_BYTES = 65000


def filter_attendance(segments, wearer_present):
    """Split a plan by whether a wearer is needed. `wearer_present=False` gives the automatable
    half, which is most of it."""
    return [s for s in segments if s.requires_wearer == wearer_present]


def resolution_plan():
    """How large a sharp region the decoder sustains at **panel-native render**.

    We already render at 99% of panel (3520 of 3552); what is unknown is how much of it can be kept
    at full resolution. Uniform full resolution is not a candidate: unfoveated panel native encodes
    7104x3840 side by side, which exceeds NVENC's 4096 H.264 width outright and extrapolates to
    ~98 ms of decode against a 16.7 ms budget.

    So the axis is `center_size` at fixed panel-native render. Centre 0.45 would encode 4544 wide
    and is excluded by the same NVENC width limit; 0.35 is the largest that fits.

    Fully automatable -- every metric here is independent of where the gaze-driven centre sits."""
    control = next(s for s in quality_plan() if s.workload == "control")
    width, height = PANEL_PER_EYE
    segments = [control]
    for centre in (0.15, 0.20, 0.25, 0.30, 0.35):
        segments.append(Segment(
            f"R-{int(centre * 100):02d}", "alvr", codec="H264", mbps=600, hz=BENCH_HZ,
            eye=width, eye_h=height, workload="screen", packet_size=TCP_SHARD_BYTES,
            center_size_x=centre, center_size_y=round(centre * 0.40 / 0.45, 3),
            note=f"panel native, centre {centre:.2f}"))

    # HEVC at the same geometry. The settled table says its decoder is ~8x slower than AVC and
    # already falls to 36 fps at 2560/eye above ~500 Mbps, so this is expected to fail -- but it is
    # two cells to measure rather than assert, and NVENC *encodes* HEVC up to 8192 wide, so the
    # encoder is not what stops it. Bracketing the range says whether it fails everywhere or only
    # above some size.
    for centre in (0.15, 0.35):
        segments.append(Segment(
            f"R-{int(centre * 100):02d}-HEVC", "alvr", codec="Hevc", mbps=600, hz=BENCH_HZ,
            eye=width, eye_h=height, workload="screen", packet_size=TCP_SHARD_BYTES,
            center_size_x=centre, center_size_y=round(centre * 0.40 / 0.45, 3),
            note=f"panel native HEVC, centre {centre:.2f}"))
    return segments


def link_plan():
    """USB against Wi-Fi at 72 Hz, at panel native and at the 2560 baseline.

    Two questions in one sitting, both from the brief:

    * **Re-bench 72 Hz at full resolution and at the 2560 baseline.** The refresh sweep measured
      panel native at 72 Hz once, over Wi-Fi; this repeats it against the older geometry so the
      two are directly comparable at the same rate.
    * **Does USB run cooler than Wi-Fi?** The CPU is the hottest thing in this headset (76.0 C
      against the video block's 69.9 C) and it holds a ~73 C floor merely awake. A wired link
      removes the Wi-Fi radio from that budget entirely, and whatever thermal headroom that buys
      is headroom every preset gets for free.

    Three transports, because the first USB run showed the interesting part is *which* USB. The
    cable moves 3739 Mbps PC->headset by raw bulk transfer (768 MiB via `adb push` in 1.64 s) and
    the controller is a USB3 dwc3, but RNDIS delivers 453 Mbps of it -- at 600 Mbps the link
    saturates, network time reaches 456 ms and the stream falls to 40 fps. RNDIS carries roughly
    one ethernet frame per USB transfer, which is the same missing batching that makes ALVR's UDP
    backend unusable and that the 65000-byte TCP shard fixed. `adb` forwards TCP over those same
    bulk endpoints, so it tests whether the loss is RNDIS rather than the bus, using code the
    harness already has.

    Note the adb arm disables the headset's Wi-Fi and asserts it has no address, so a USB run
    cannot quietly fall back to wireless and report a flattering number.

    A caution on what cooler running would and would not unblock: HEVC's problem here is decode
    *throughput*, not temperature. It falls to 36 fps above ~500 Mbps at 2560/eye because the
    hardware decoder is ~8x slower than the AVC one -- a silicon limit that a cooler headset does
    not move. Decode times do drift upward with heat, so headroom helps at the margin, but nobody
    should expect 10 C to close an 8x gap. `resolution_plan` already carries two HEVC cells for
    when that is worth retesting.

    Transports alternate rather than grouping, so thermal drift across the sitting cannot
    masquerade as a transport difference -- the failure mode that confounded the first shard-size
    comparison, where one cell started 24 C cooler than the other and both ended at 73-74 C.

    Automatable: nothing here depends on where the gaze-driven centre sits."""
    control = next(s for s in quality_plan() if s.workload == "control")
    native_w, native_h = PANEL_PER_EYE
    geometries = (("NATIVE", native_w, native_h, 0.20), ("2560", 2560, 2560, 0.45))
    segments = [control]
    for label, width, height, centre in geometries:
        for transport, name in (("wifi", "WIFI"), ("rndis", "USB"), ("adb", "ADB")):
            segments.append(Segment(
                f"L-{name}-{label}", "alvr", codec="H264", mbps=600, hz=BENCH_HZ,
                eye=width, eye_h=height, transport=transport, workload="screen",
                packet_size=TCP_SHARD_BYTES,
                center_size_x=centre, center_size_y=round(centre * 0.40 / 0.45, 3),
                note=f"{transport} {width}x{height}/eye, centre {centre:.2f}"))
    # transport is the inner loop, so consecutive segments already alternate it. Geometry cannot
    # alternate as well -- with four distinct cells in a 2x2 no order flips both every step -- and
    # transport is the subtler comparison of the two, so it gets the protection.
    return segments


def transport_thermal_plan():
    """What actually drives the headset's CPU heat: the protocol, or the shard size?

    As measured, the CPU was the hottest thing in the headset at 76.0 C against 69.9 C for
    the video block, and decode time was flat across a 19% pixel increase. So the load is not
    decoding. The two candidates on the receive path:

    * **Protocol.** These runs are TCP. Virtual Desktop and Steam Link both use UDP, and at
      600 Mbps TCP is doing acknowledgements, reordering and congestion control in the kernel --
      all on the CPU that is running hottest. UDP does none of it.
    * **Shard size.** ALVR splits each frame into `packet_size` writes, so 600 Mbps at the default
      1400 B is roughly 53,000 packets a second.

    Three cells separate them: hold everything else fixed, change one thing at a time.

        P-TCP-1400    baseline, today's configuration
        P-TCP-65000   same protocol, ~46x fewer packets  -> isolates shard size
        P-UDP-1400    same shard size, no TCP machinery  -> isolates protocol

    UDP stays at 1400 deliberately: above the MTU it fragments at the IP layer, which would add
    back the per-packet cost the test is trying to remove and confound the comparison.

    Read `thermal_rise_c` for the answer, and watch `packets_lost` -- UDP has no retransmission, so
    a thermal win that costs picture integrity is not a win. Fully automatable."""
    width, height = PANEL_PER_EYE
    common = dict(codec="H264", mbps=600, hz=BENCH_HZ, eye=width, eye_h=height, workload="screen",
                  center_size_x=0.20, center_size_y=0.178)
    return [
        Segment("P-TCP-1400", "alvr", protocol="Tcp", packet_size=1400,
                note="baseline: TCP, 1400 B shards (~53,571/s)", **common),
        Segment("P-TCP-65000", "alvr", protocol="Tcp", packet_size=65000,
                note="TCP, 65000 B shards (~1,153/s) -- isolates shard size", **common),
        Segment("P-UDP-1400", "alvr", protocol="Udp", packet_size=1400,
                note="UDP, 1400 B shards -- isolates protocol", **common),
    ]


def transport_verify_plan():
    """The TCP shard-size result again with the order reversed, as a control.

    In the first run `P-TCP-65000` started 4.4 C hotter than the baseline (74.3 vs 69.9), so its
    zero thermal rise is partly just less headroom to rise into. Running 65000 *first*, from the
    cooler start, separates the two: if the effect follows the shard size it survives the swap; if
    it follows position in the run, it was temperature all along.

    The frame-drop result (fps min 30.00 -> 59.54) does not depend on temperature, so this is
    really a check on the thermal half. Each label is suffixed so it cannot be confused with the
    first run's data."""
    width, height = PANEL_PER_EYE
    common = dict(codec="H264", mbps=600, hz=BENCH_HZ, eye=width, eye_h=height, workload="screen",
                  protocol="Tcp", center_size_x=0.20, center_size_y=0.178)
    return [
        Segment("V-TCP-65000", "alvr", packet_size=65000,
                note="65000 B shards, run FIRST from the cold start", **common),
        Segment("V-TCP-1400", "alvr", packet_size=1400,
                note="1400 B shards, run SECOND -- order reversed vs transport1", **common),
    ]


def packet_thermal_plan():
    """Does shard size drive the headset's CPU heat? One variable, everything else fixed.

    Measured straight after a heavy sweep, the CPU was the hottest thing in the headset
    at 76.0 C against 69.9 C for the video block. So the decode engine is *not* what throttles, and
    cutting encoded pixels is the wrong lever -- which matches the same morning's resolution sweep,
    where decode time was flat across a 19% pixel increase but rose with temperature.

    The obvious CPU load is packet handling. ALVR shards every frame into `packet_size` writes, so
    600 Mbps at the default 1400 B is roughly **53,000 packets a second**, and 800 Mbps is ~71,000
    -- a lot of interrupts and syscalls. `packet_size` is already a setting; 1400 suits UDP, but
    these runs are TCP, where it buys nothing and costs per-packet overhead.

    If larger shards cut the CPU temperature rise at identical geometry and bitrate, that is a
    thermal lever worth far more than resolution. If they do not, the heat is elsewhere and this
    rules it out cheaply. Either way `thermal_rise_c` answers it in degrees rather than in a 0-3
    status. Fully automatable."""
    width, height = PANEL_PER_EYE
    return [
        Segment(f"P-{packet}", "alvr", codec="H264", mbps=600, hz=BENCH_HZ,
                eye=width, eye_h=height, workload="screen",
                center_size_x=0.20, center_size_y=0.178, packet_size=packet,
                note=f"panel native, {packet} B shards (~{600_000_000 // (packet * 8):,}/s)")
        for packet in (1400, 8192, 32768, 65000)
    ]


def buffering_plan():
    """How much of the latency budget is deliberate queue?

    The stage breakdown of the Wi-Fi runs put 39.9 ms of a 98.4 ms frame in queuing --
    decoder_queue 16.7 plus vsync_queue 23.2 -- against 14.8 ms of actual decoding. That is the
    largest term by far and the one nothing else in this project has touched, while the project's bar
    is 60 ms.

    `max_buffering_frames` is what governs it, and ALVR's own help is explicit: "Increasing this
    value will help reduce stutter but it will increase latency." Every measurement to date used
    the 2.0 default, which at 72 Hz is 27.8 ms of intentional delay. The floor is 1.0
    (settings.rs slider min), so the most this can return is one frame period, 13.9 ms.

    That will not reach 60 ms on its own -- 98.4 minus 13.9 is still 84.5 -- and saying so in
    advance is the point: this measures how much of the queue is recoverable and what it costs in
    stutter, so the next lever can be chosen on evidence. Watch `fps_min` and `packets_lost`, since
    a shallower buffer is exactly what a jittery link would expose.

    Geometry is held at the panel-native reference cell that measured 72.00 fps / 14.80 ms decode,
    so any change in total latency is attributable to the buffer alone.

    Automatable: nothing here depends on where the gaze-driven centre sits."""
    control = next(s for s in quality_plan() if s.workload == "control")
    width, height = PANEL_PER_EYE
    segments = [control]
    for frames in (2.0, 1.5, 1.25, 1.0):
        segments.append(Segment(
            f"B-{str(frames).replace('.', '')}", "alvr", codec="H264", mbps=600, hz=BENCH_HZ,
            eye=width, eye_h=height, workload="screen", packet_size=TCP_SHARD_BYTES,
            center_size_x=0.20, center_size_y=0.178, buffering=frames,
            note=f"panel native, {frames} buffered frames "
                 f"({frames * 1000.0 / BENCH_HZ:.1f} ms of queue)"))
    return segments


def pyro_plan():
    """PyroWave over UDP at the panel-native B-15 geometry, bitrate rising 100 -> 600 Mbps.

    Owner's call: Pyro only, no H.264 arm, and climb rather than start at the top: the
    cheap cells run on the coolest headset, and a cliff shows up as the step where fps or decode
    gives way. The harness cools the headset between segments and logs zones before and after
    every one. The codec and the transport were the server's env (ALVR_PYROWAVE=1,
    ALVR_PYROWAVE_UDP=1) when this ran; the beta build reads both from the session."""
    base = next(s for s in buffering_plan() if s.label == "B-15")
    return [Segment(
        f"P-{mbps}", "alvr", codec="PyroWave", mbps=mbps, hz=base.hz,
        eye=base.eye, eye_h=base.eye_h, workload=base.workload,
        packet_size=base.packet_size, center_size_x=base.center_size_x,
        center_size_y=base.center_size_y, buffering=base.buffering,
        note=f"PyroWave over UDP at the B-15 geometry, {mbps} Mbps")
        for mbps in (100, 200, 300, 400, 500, 600)]


def pyro_latestart_plan():
    """latestart_plan for PyroWave over UDP: how late can the server start a frame now?

    Measured at the worn 100 Mbps cell, PyroWave's total is 77 ms of which 21 ms is
    work and 56 ms is vsync-aligned waiting (game_time 17, decoder_queue 8, vsync_queue 32), so
    the 60 ms bar is a pacing problem. `pacing_delay_us` moves `input_acquired` later, the one
    lever that shortens `predicted_display_time - input_acquired` itself. Expect total to fall
    ~1:1 with the delay until frames miss the display deadline; the knee is the measurement.

    200 Mbps: the ladder held 72 fps and 13.6 ms decode there, and the 300 Mbps verify segment
    skipped 22 % of frames on a hot GPU, which would confound a latency knee with a decode one.
    Every cell needs a wearer; the latency of an unworn segment does not count."""
    base = next(s for s in pyro_plan() if s.mbps == 200)
    return [Segment(
        f"PL-{micros // 1000}", "alvr", codec="PyroWave", mbps=200, hz=base.hz,
        eye=base.eye, eye_h=base.eye_h, workload=base.workload,
        packet_size=base.packet_size, center_size_x=base.center_size_x,
        center_size_y=base.center_size_y, buffering=base.buffering,
        pacing_delay_us=micros,
        note=f"PyroWave UDP 200 Mbps, frame start delayed {micros / 1000:.0f} ms")
        for micros in (0, 4000, 8000, 12000, 16000, 20000)]


def pyro_phase_plan():
    """Phase lock on/off at the PL-0 cell, two rounds interleaved so heat cannot side with either
    arm. The measurement is `total_ms` net of `game_time`, plus `decoder_queue` collapsing toward
    the 2 ms target and `vsync_queue` no longer shrinking, with fps held at 72."""
    base = next(s for s in pyro_latestart_plan() if s.pacing_delay_us == 0)
    segs = []
    for round_ in (1, 2):
        for lock in (False, True):
            segs.append(Segment(
                f"PP-{'ON' if lock else 'OFF'}-{round_}", "alvr", codec="PyroWave", mbps=base.mbps,
                hz=base.hz, eye=base.eye, eye_h=base.eye_h, workload=base.workload,
                packet_size=base.packet_size, center_size_x=base.center_size_x,
                center_size_y=base.center_size_y, buffering=base.buffering, phase_lock=lock,
                note=f"PyroWave UDP {base.mbps} Mbps, phase lock {'on' if lock else 'off'}, round {round_}"))
    return segs


def pyro_earlypoll_plan():
    """Frame-first vs stock loop order, two rounds interleaved, unattended (sensor covered).
    The measurement is `vsync_queue` and `total_ms` with fps held at 72; the ceiling is one
    frame period."""
    base = next(s for s in pyro_latestart_plan() if s.pacing_delay_us == 0)
    segs = []
    for round_ in (1, 2):
        for early in (False, True):
            segs.append(Segment(
                f"PE-{'ON' if early else 'OFF'}-{round_}", "alvr", codec="PyroWave", mbps=base.mbps,
                hz=base.hz, eye=base.eye, eye_h=base.eye_h, workload=base.workload,
                packet_size=base.packet_size, center_size_x=base.center_size_x,
                center_size_y=base.center_size_y, buffering=base.buffering, early_poll=early,
                note=f"PyroWave UDP {base.mbps} Mbps, early poll {'on' if early else 'off'}, round {round_}"))
    return segs


def pyro_hz_plan():
    """72 vs 90 Hz at 100 and 200 Mbps, interleaved, unattended.

    Three pacing levers moved nothing at 72 Hz: the total is ~21 ms of work plus the
    runtime's lead (~2.2 periods) plus one server frame period. Only the period itself moves those
    two, so 90 Hz is the one arithmetic that reaches 60 ms -- if decode fits 11.1 ms. Watch
    `client_fps` (must hold the rate), `decoder_ms`, and the receiver's skipped-frame count."""
    base = next(s for s in pyro_plan() if s.mbps == 100)
    segs = []
    for mbps in (100, 200):
        for hz in (72, 90):
            segs.append(Segment(
                f"H{hz}-{mbps}", "alvr", codec="PyroWave", mbps=mbps, hz=hz,
                eye=base.eye, eye_h=base.eye_h, workload=base.workload,
                packet_size=base.packet_size, center_size_x=base.center_size_x,
                center_size_y=base.center_size_y, buffering=base.buffering,
                note=f"PyroWave UDP {mbps} Mbps at {hz} Hz"))
    return segs


def pyro_res_plan():
    """Extreme resolution-for-bitrate: render well under panel native and spend the bitrate.

    Owner's spec: 60 % linear (2131x2304/eye, ~36 % of the panel's pixels) at 600 Mbps,
    72 Hz, everything else as streamed today (4:4:4 for now). The questions are whether cutting
    64 % of the pixels drops Adreno decode time and heat, whether 72 fps then holds worn and
    thermally settled, and whether 600 Mbps makes the soft render look close to panel native.
    If it does, the same cell at 90 Hz; if it is too soft, 70 % (2486x2688, ~49 % of the pixels).
    Labels: X<percent>-<mbps>[-90]."""
    width, height = PANEL_PER_EYE
    base = next(s for s in pyro_plan() if s.mbps == 600)
    segs = []
    for pct, mbps, hz in ((60, 600, 72), (60, 600, 90), (60, 400, 90), (60, 300, 90), (70, 600, 72), (70, 600, 90)):
        eye = int(round(width * pct / 100)); eye_h = int(round(height * pct / 100))
        segs.append(Segment(
            f"X{pct}-{mbps}" + ("-90" if hz == 90 else ""), "alvr", codec="PyroWave", mbps=mbps, hz=hz,
            eye=eye, eye_h=eye_h, workload=base.workload, packet_size=base.packet_size,
            center_size_x=base.center_size_x, center_size_y=base.center_size_y,
            buffering=base.buffering,
            note=f"PyroWave UDP {mbps} Mbps, {pct} % linear ({eye}x{eye_h}/eye), {hz} Hz"))
    # Sustained: the candidate operating point for five minutes, so thermals are measured from a
    # warm start rather than the cool 41 C the 90 s cell happened to begin at.
    x = next(s for s in segs if s.label == "X60-400-90")
    segs.append(Segment(
        "S60-400-90", "alvr", codec="PyroWave", mbps=x.mbps, hz=x.hz, eye=x.eye, eye_h=x.eye_h,
        workload=x.workload, packet_size=x.packet_size, center_size_x=x.center_size_x,
        center_size_y=x.center_size_y, buffering=x.buffering, scene_seconds=300,
        note="candidate operating point, five minutes sustained"))
    return segs


AB_PHOTO = str(paths.ROOT / "xrbench" / "ab_photo.png")


def pyro_ab_plan():
    """Image-quality A/B, 300 vs 400 Mbps, at the candidate operating point (60 % linear, 90 Hz),
    two rounds interleaved, on the photo layout (natural detail, hair, fences, small text). Tap
    the bitstream (`--tap`) so the decoded frames can be scored against the panel offline."""
    base = next(s for s in pyro_res_plan() if s.label == "X60-400-90")
    segs = []
    for round_ in (1, 2):
        for mbps in (300, 400):
            segs.append(Segment(
                f"AB-{mbps}-{round_}", "alvr", codec="PyroWave", mbps=mbps, hz=base.hz,
                eye=base.eye, eye_h=base.eye_h, workload=base.workload, packet_size=base.packet_size,
                center_size_x=base.center_size_x, center_size_y=base.center_size_y,
                buffering=base.buffering, photo=AB_PHOTO,
                note=f"A/B image quality, {mbps} Mbps, round {round_}"))
    return segs


def pyro_live3_plan():
    """The three chosen live cells: 2560^2 @ 300, 2304^2 @ 300, 2304^2 @ 243 Mbps at
    90 Hz -- the offline matrix's constant-bpp point for 2304 is 243 Mbps (300 x 0.81). Square
    per-eye renders, everything else as the pipeline runs (gaze foveation on), photo panel so the
    tapped frames score against the same panel offline. Worn: the tester is the subjective arm."""
    base = next(s for s in pyro_res_plan() if s.label == "X60-400-90")
    return [Segment(
        f"L{res}-{mbps}", "alvr", codec="PyroWave", mbps=mbps, hz=90, eye=res, eye_h=res,
        workload=base.workload, packet_size=base.packet_size, center_size_x=base.center_size_x,
        center_size_y=base.center_size_y, buffering=base.buffering, photo=AB_PHOTO,
        note=f"{res}x{res}/eye at {mbps} Mbps, 90 Hz")
        for res, mbps in ((2560, 300), (2304, 300), (2304, 243))]


def pyro_path_plan():
    """Experiment 1: fragment vs compute reconstruction of identical bytes at the candidate
    operating point (X60-400-90), alternating so ordering and heat cannot side with one arm.
    PF/PC are the 90 s cells; SF/SC are the five-minute sustained cells for Phase 3."""
    base = next(s for s in pyro_res_plan() if s.label == "X60-400-90")
    def cell(label, path, seconds=base.scene_seconds):
        return Segment(label, "alvr", codec="PyroWave", mbps=base.mbps, hz=base.hz, eye=base.eye, eye_h=base.eye_h,
                       workload=base.workload, packet_size=base.packet_size, center_size_x=base.center_size_x,
                       center_size_y=base.center_size_y, buffering=base.buffering, decode_path=path,
                       scene_seconds=seconds, note=f"Experiment 1: {path} path, {seconds} s")
    return [cell("PF-1", "fragment"), cell("PC-1", "compute"), cell("PF-2", "fragment"), cell("PC-2", "compute"),
            cell("SF", "fragment", 300), cell("SC", "compute", 300)]


def home_operating_point(label, mbps, eye=None, eye_h=None, seconds=None, note=""):
    """The adopted operating point (X60-400-90, compute path) on static SteamVR Home, with only the
    per-eye render size and the bitrate open. Everything else -- 4:4:4, 90 Hz, PyroWave CDF 9/7,
    UDP, gaze foveation 0.20/0.178 with the same edge ratios and packet size -- stays fixed."""
    base = next(s for s in pyro_res_plan() if s.label == "X60-400-90")
    return Segment(label, "alvr", codec="PyroWave", mbps=mbps, hz=base.hz, eye=eye or base.eye,
                   eye_h=eye_h or base.eye_h, workload=base.workload, packet_size=base.packet_size,
                   center_size_x=base.center_size_x, center_size_y=base.center_size_y,
                   edge_ratio_x=base.edge_ratio_x, edge_ratio_y=base.edge_ratio_y, buffering=base.buffering,
                   decode_path="compute", scene="home", scene_seconds=seconds or base.scene_seconds, note=note)


def pyro_home_baseline_plan():
    """Replicated Home baselines at the operating point: 400 and 300 Mbps alternated three times so
    ordering and heat cannot side with one bitrate, then five minutes sustained at 400. Results are
    SteamVR Home results, not gameplay results."""
    segs = []
    for rep in (1, 2, 3):
        for mbps in (400, 300):
            segs.append(home_operating_point(f"HB{mbps}-{rep}", mbps, note=f"Home baseline {mbps} Mbps, replicate {rep}"))
    segs.append(home_operating_point("SHB400", 400, seconds=300, note="Home baseline 400 Mbps, five minutes sustained"))
    return segs


# Panel-aspect ladder: per-eye render = pct of the 3552x3840 panel, the
# operating point's foveation kept, so ALVR's own 32-pixel alignment yields the encode size:
# 100 % 3328x1472, 90 % 3008x1344, 80 % 2688x1184, 70 % 2368x1056, 60 % 1984x896 (side by side).
MATRIX_PCTS = (100, 90, 80, 70, 60)
MATRIX_MBPS = (300, 400)
# Interleaved: no resolution twice in a row, bitrate alternating, so heat and link drift spread
# across the grid instead of lining up with one axis.
MATRIX_ORDER = ((60, 400), (100, 300), (80, 400), (70, 300), (90, 400),
                (60, 300), (100, 400), (80, 300), (70, 400), (90, 300))


def pyro_matrix_plan():
    """Panel-aspect resolution x bitrate matrix on static SteamVR Home: five render scales x
    300/400 Mbps, only spatial scale and bitrate varying, closed by a repeat of the first cell to
    measure drift across the run. 4:2:0 vs 4:4:4 and 72 vs 90 Hz are not variables here."""
    width, height = PANEL_PER_EYE
    def cell(pct, mbps, suffix=""):
        eye, eye_h = int(round(width * pct / 100)), int(round(height * pct / 100))
        return home_operating_point(f"PM{pct}-{mbps}{suffix}", mbps, eye=eye, eye_h=eye_h,
                                    note=f"matrix {pct} % panel ({eye}x{eye_h}/eye), {mbps} Mbps, Home")
    return [cell(p, m) for p, m in MATRIX_ORDER] + [cell(*MATRIX_ORDER[0], suffix="-R")]


QUALITY_FRAMES = 30


def pyro_matrix_quality_plan():
    """Objective-quality companion to the matrix: the same ten cells (labels PQ...), each dumping
    QUALITY_FRAMES lossless frames inside the Home window; run with --tap so the frames the headset
    was sent are recorded too. The dump's readback stalls the encoder, so these cells are for
    quality only, never timing."""
    return [replace(s, label=s.label.replace("PM", "PQ", 1), quality_frames=QUALITY_FRAMES, scene_seconds=20,
                    note=s.note.replace("matrix", "matrix quality", 1))
            for s in pyro_matrix_plan() if not s.label.endswith("-R")]


CORPUS_ROOT = str(paths.ROOT / "corpus")


def corpus_plan():
    """Experiment 4 real-content corpus, the classes the pipeline renders without a wearer.
    Each cell streams the operating-point configuration and dumps a 90-frame lossless clip of
    the encoder's input: the synthetic panel (test pattern), the photo layout (high-detail
    textures), and SteamVR Home after the scene exits (room scene; `game_seconds` keeps the
    stream alive and the dump start lands inside it). Worn gameplay classes are captured at the
    session the tester picks titles for, with `--dump-label`."""
    base = next(s for s in pyro_res_plan() if s.label == "X60-400-90")
    def cell(label, klass, start, photo="", game_seconds=0, note=""):
        return Segment(label, "alvr", codec="PyroWave", mbps=base.mbps, hz=base.hz, eye=base.eye, eye_h=base.eye_h,
                       workload=base.workload, packet_size=base.packet_size, center_size_x=base.center_size_x,
                       center_size_y=base.center_size_y, buffering=base.buffering, decode_path="compute",
                       photo=photo, game_seconds=game_seconds, dump_frames=90, dump_start=start, dump_label=klass,
                       note=note or f"corpus clip: {klass}")
    return [cell("CP-PANEL", "synthetic_panel", 200),
            cell("CP-PHOTO", "textures_photo", 200, photo=AB_PHOTO),
            cell("CP-HOME", "steamvr_home", 90 * 45, game_seconds=60, note="corpus clip: SteamVR Home after the scene exits")]


def pyro_53_plan():
    """Experiment 2: CDF 9/7 at PyroWave's default precision (FP32 math, FP16 storage on two
    levels) against CDF 5/3 with all-FP16 math and storage, both on the compute path, at the
    candidate operating point (X60-400-90), alternating as Experiment 1 did. P97/P53 are the short
    paired cells; S97/S53 the five-minute sustained cells for Phase 3."""
    base = next(s for s in pyro_res_plan() if s.label == "X60-400-90")
    def cell(label, wavelet, seconds=base.scene_seconds):
        precision = 0 if wavelet == "53" else 1
        return Segment(label, "alvr", codec="PyroWave", mbps=base.mbps, hz=base.hz, eye=base.eye, eye_h=base.eye_h,
                       workload=base.workload, packet_size=base.packet_size, center_size_x=base.center_size_x,
                       center_size_y=base.center_size_y, buffering=base.buffering, decode_path="compute",
                       wavelet=wavelet, pyro_precision=precision, scene_seconds=seconds,
                       note=f"Experiment 2: CDF {wavelet[0]}/{wavelet[1]} precision {precision}, {seconds} s")
    return [cell("P97-1", "97"), cell("P53-1", "53"), cell("P97-2", "97"), cell("P53-2", "53"),
            cell("S97", "97", 300), cell("S53", "53", 300)]


def pacing_plan():
    """The two queuing levers, crossed, since they may not be independent.

    The stage breakdown put 39.9 ms of a 98.4 ms frame in queuing. Two settings govern it:

    * `max_buffering_frames`, 2.0 by default, which at 72 Hz is 27.8 ms of intentional queue.
    * `enforce_server_frame_pacing`, true by default, which makes the server sleep until the next
      vsync before producing a frame (server_openvr/src/lib.rs:592) rather than yielding -- up to
      another frame period, 13.9 ms.

    A 2x2 rather than two separate sweeps because the levers plausibly overlap: pacing delays frame
    *production* and buffering delays frame *consumption*, so shallowing the buffer may simply
    expose the pacing sleep and return less than its arithmetic suggests. Running them crossed is
    the only way to find out, and it is four cells rather than six.

    The (on, 2.0) cell is exactly today's default, measured in the same sitting so the other three
    are comparable against it rather than against runs taken at another temperature.

    Watch `fps_min` and `packets_lost`: both levers exist to absorb jitter, and removing them is
    expected to cost something. The question is how much, against roughly 42 ms of latency.

    Automatable: nothing here depends on where the gaze-driven centre sits."""
    control = next(s for s in quality_plan() if s.workload == "control")
    width, height = PANEL_PER_EYE
    segments = [control]
    for pacing in (True, False):
        for frames in (2.0, 1.0):
            name = f"P-{'ON' if pacing else 'OFF'}-{str(frames).replace('.', '')}"
            segments.append(Segment(
                name, "alvr", codec="H264", mbps=600, hz=BENCH_HZ,
                eye=width, eye_h=height, workload="screen", packet_size=TCP_SHARD_BYTES,
                center_size_x=0.20, center_size_y=0.178,
                buffering=frames, frame_pacing=pacing,
                note=f"panel native, pacing {'on' if pacing else 'off'}, {frames} buffered frames"))
    return segments


def headroom_plan():
    """How much latency a bounded pacing headroom returns, and what it costs.

    Turning frame pacing off entirely is not usable: as measured, the server free-ran,
    overshot a 600 Mbps target to 1356 Mbps and blew network time to 112 ms for a 194 ms total.
    But the all-or-nothing choice is an artifact of the code, not a necessity --
    `duration_until_next_vsync` keeps a self-correcting virtual clock, advancing `last_vsync_time`
    until it is ahead of `now` and returning the remainder. Sleeping *short* therefore yields a
    longer wait on the next call, and the loop settles at one frame per interval shifted earlier,
    not at more frames per second.

    So this sweeps the shift. 0 is today's behaviour and the reference. The cells stay well inside
    half a frame period (13.89 ms at 72 Hz) because past some fraction of it frames begin arriving
    before the previous one is consumed, which rebuilds the queue that pacing exists to prevent.

    Expect single-digit milliseconds. `fps_min`, `packets_lost` and achieved bitrate are what would
    show the headroom being taken too far -- a drift toward the 1356 Mbps free-running figure means
    the clock is no longer holding the rate.

    Automatable: nothing here depends on where the gaze-driven centre sits."""
    control = next(s for s in quality_plan() if s.workload == "control")
    width, height = PANEL_PER_EYE
    segments = [control]
    for micros in (0, 2000, 4000, 6000):
        segments.append(Segment(
            f"H-{micros // 1000}", "alvr", codec="H264", mbps=600, hz=BENCH_HZ,
            eye=width, eye_h=height, workload="screen", packet_size=TCP_SHARD_BYTES,
            center_size_x=0.20, center_size_y=0.178, buffering=1.5,
            pacing_headroom_us=micros,
            note=f"panel native, pacing headroom {micros / 1000:.0f} ms"))
    return segments


def latestart_plan():
    """How late can the server start a frame before it misses the display deadline?

    Total latency is `predicted_display_time - input_acquired` and contains no stage duration, so
    making a stage faster only moves the waiting. As measured, every exposed pacing
    control confirmed it: buffering, pacing on/off and pacing headroom each redistributed time
    between stages and left the total within noise of 80 ms.

    The client's tracking thread free-runs at 3x refresh with no relationship to any deadline
    (`stream_input_loop`: `deadline += frame_interval / 3`), so `input_acquired` is just the newest
    sample when the server starts a frame, within one 4.63 ms tick. Starting the server later
    therefore moves `input_acquired` later -- the one lever that shortens the interval itself.

    Expect total to fall roughly 1:1 with the delay until frames begin missing their display
    deadline, then jump by a frame period as they slip. **The knee is the measurement**: it says
    how much early-start margin the pipeline carries today for no benefit, and PyroWave's value is
    then how much further it moves that knee.

    Watch `fps_min` and `packets_lost` for the far cells, and read totals net of `game_time` --
    it is an uncontrolled input that swung 0.7-109 ms across the pacing sweep and confounded it.
    """
    control = next(s for s in quality_plan() if s.workload == "control")
    width, height = PANEL_PER_EYE
    segments = [control]
    for micros in (0, 2000, 4000, 6000, 8000, 10000):
        segments.append(Segment(
            f"L-{micros // 1000}", "alvr", codec="H264", mbps=600, hz=BENCH_HZ,
            eye=width, eye_h=height, workload="screen", packet_size=TCP_SHARD_BYTES,
            center_size_x=0.20, center_size_y=0.178, buffering=1.5,
            pacing_delay_us=micros,
            note=f"panel native, frame start delayed {micros / 1000:.0f} ms"))
    return segments


def upscaling_plan():
    """Does SGSR buy fidelity for free?

    `graphics/resources/stream.wgsl` already contains Snapdragon Game Super Resolution v1 --
    a 4-tap edge vote with fast-Lanczos weights and a clamped luma delta, i.e. edge-directed
    sharpening plus upscale. It runs inside the same fragment invocation as the foveation inverse
    warp, so it costs no extra pass, and ALVR ships it switched off (settings.rs upscaling.enabled
    false).

    The appeal here is that it is the one quality lever that does not touch the decoder, which is
    the constraint everything else in this project runs into. `upscale_factor` scales the
    *swapchain*, so the composite pass runs factor^2 more fragments; the encoded frame is
    unchanged. `client_compositor_ms` measured 1.4 ms against a 13.89 ms period, so there is room.

    Geometry is held at the panel-native reference cell. The check that matters is negative:
    `encoded_mpx` and `decoder_ms` must be identical across arms. If either moves, the axis is
    wired wrong and is buying decode cost rather than free sharpening.

    Whether it *looks* better is the tester's call, as with the rest of the ladder -- the objective
    still-capture path is broken. What this plan proves is that it is affordable.

    Automatable: nothing here depends on where the gaze-driven centre sits."""
    control = next(s for s in quality_plan() if s.workload == "control")
    width, height = PANEL_PER_EYE
    common = dict(codec="H264", mbps=600, hz=BENCH_HZ, eye=width, eye_h=height,
                  workload="screen", packet_size=TCP_SHARD_BYTES, buffering=1.5,
                  center_size_x=0.20, center_size_y=0.178)
    segments = [control, Segment("U-OFF", "alvr", upscaling=False, **common,
                                 note="panel native, upscaling off -- the reference")]
    for factor in (1.25, 1.5):
        segments.append(Segment(
            f"U-{int(factor * 100)}", "alvr", upscaling=True, upscale_factor=factor, **common,
            note=f"panel native, SGSR x{factor:.2f} ({factor ** 2:.2f}x composite fragments)"))
    return segments


def thermal_plan():
    """The Giant Screen ladder bracketed by the *same* control segment, first and last.

    Separates heat from configuration. Measured: `Q-SCREEN-GOOD` and `Q-SCREEN-BETTER`
    encode identical frame dimensions (1696x1568) yet decoded in 17.81 ms and 20.61 ms, tracking
    thermal status 2 -> 3. Suggestive, but they also differ in bitrate (400 vs 600 Mbps) and
    entropy decoding scales with bits as well as pixels, so that comparison is confounded.

    Two runs of an identical segment are not. If the closing one is slower, it is heat, and nothing
    else is left to explain it. The tester independently observed SteamVR Home still stuttering after a
    run, at unrelated settings, which no decode-budget explanation covers.

    Why it matters beyond this run: decode timings that drift with temperature make a ladder
    unfair, because the segments measured last are penalised -- and in a sharpness ladder those are
    exactly the sharpest presets. If this confirms, segment order has to be randomised."""
    presets = quality_plan()
    control = next(s for s in presets if s.workload == "control")
    ladder = [s for s in presets if s.workload == "screen"]
    closing = replace(control, label="Q-CONTROL-END",
                      note="control repeat, run last -- same config, so any difference is heat")
    return [control] + ladder + [closing]


def workload_plan(workload):
    """One workload class, always led by the control so a regression is visible immediately."""
    control = [s for s in quality_plan() if s.workload == "control"]
    return control + [s for s in quality_plan() if s.workload == workload]


def validate(segments):
    seen = set()
    for s in segments:
        if s.label in seen:
            raise ValueError(f"duplicate label {s.label}")
        seen.add(s.label)
        if s.stack not in ("alvr", "vd"):
            raise ValueError(f"{s.label}: unknown stack {s.stack}")
        if s.transport not in ("wifi", "rndis", "adb"):
            raise ValueError(f"{s.label}: unknown transport {s.transport}")
        if not 500 <= s.packet_size <= 65000:
            raise ValueError(f"{s.label}: packet_size {s.packet_size} outside 500..65000")
        if s.stack != "alvr":
            continue
        if s.codec not in ("H264", "Hevc", "PyroWave"):
            raise ValueError(f"{s.label}: codec {s.codec}")
        if s.wavelet not in ("97", "53"):
            raise ValueError(f"{s.label}: wavelet {s.wavelet} (97 or 53)")
        if s.pyro_precision not in (-1, 0, 1, 2):
            raise ValueError(f"{s.label}: pyro_precision {s.pyro_precision} (-1, 0, 1 or 2)")
        if s.codec != "PyroWave" and s.mbps > NVENC_H264_MAX_MBPS:
            raise ValueError(f"{s.label}: {s.mbps} Mbps exceeds NVENC's {NVENC_H264_MAX_MBPS}")
        if s.workload not in ("control", "full", "seated", "seated_quality", "screen"):
            raise ValueError(f"{s.label}: unknown workload {s.workload}")
        if s.codec == "H264":
            # the segment's own shape, not FoveationConfig()'s defaults: a tighter centre encodes
            # smaller, so a resolution illegal at 0.45 can be perfectly legal here
            width, height = s.eye_size()
            if not fits_limit(width, height, s.foveation_config(), NVENC_H264_LIMIT):
                raise ValueError(f"{s.label}: {width}x{height}/eye encodes larger than "
                                 f"{NVENC_H264_LIMIT[0]}x{NVENC_H264_LIMIT[1]} for H.264")


def estimate_minutes(segments):
    total = SETUP_MINUTES + sum(s.minutes() for s in segments)
    if any(s.stack == "vd" for s in segments):
        total += SWITCH_TO_VD_MINUTES
    return total


def save(segments, path):
    with open(path, "w") as handle:
        json.dump([asdict(s) for s in segments], handle, indent=2)


def load(path):
    with open(path) as handle:
        return [Segment(**item) for item in json.load(handle)]


def beta_validation_plan():
    """The beta build as a tester runs it, with no research overrides: the recommended profile three
    times, the tuned H.264 profile once, then each PyroWave experiment control flipped once on the
    recommended profile. Home results; the toggle cells show the setting reaches both sides."""
    rec = replace(home_operating_point("BV-PW-1", 400), settings_only=True, pyro_transport="Udp",
                  wavelet="97", decode_path="compute", gaze=True)
    width, height = PANEL_PER_EYE
    segs = [replace(rec, label=f"BV-PW-{i}", note=f"recommended profile, replicate {i}") for i in (1, 2, 3)]
    segs.append(replace(rec, label="BV-H264", codec="H264", mbps=600, hz=72, eye=width, eye_h=height,
                        buffering=1.5, packet_size=TCP_SHARD_BYTES, protocol="Tcp", nvenc_preset=1,
                        note="ALVR H.264 (tuned) profile"))
    segs.append(replace(rec, label="BV-TCP", pyro_transport="Tcp", note="PyroWave over TCP"))
    segs.append(replace(rec, label="BV-53", wavelet="53", note="CDF 5/3"))
    segs.append(replace(rec, label="BV-FRAG", decode_path="fragment", note="fragment decode path"))
    eye70, eye70_h = (int(round(width * 0.7)), int(round(height * 0.7)))
    segs.append(replace(rec, label="BV-70", eye=eye70, eye_h=eye70_h, note="70 % render scale"))
    return segs
