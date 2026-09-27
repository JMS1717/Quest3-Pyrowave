package com.xrwired.receiverxr;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Process;
import android.os.SystemClock;
import android.util.Log;
import android.view.Surface;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Receives the XRWired video stream over TCP and decodes it onto a surface owned by the native
 * OpenXR layer. The wire protocol is the one the sender (tools/send_video.py) already speaks:
 *
 * <pre>
 *   header  "XRW1" u32 width u32 height u32 fps                     (AVC)
 *           "XRW2" u32 width u32 height u32 fps u32 codec           (codec 0 = AVC, 1 = MV-HEVC,
 *                                                                    width/height are PER EYE)
 *   records u32 length, u64 pts_us, Annex B access unit
 *   acks    (same socket, back to the sender) u8 kind, u64 pts_us   (kind 1 = decoded, 2 = displayed)
 * </pre>
 *
 * Nothing here touches the UI: the caller hands in a {@link SurfaceSource} that blocks until the
 * swapchain/video surface exists, which keeps this class usable from the native render loop.
 */
public final class StreamReceiver {
    static final String TAG = "XRWiredXR";
    private static final int PORT = 45100;
    static final int MAX_AU = 16 * 1024 * 1024;

    /** Supplies the decoder output surface; blocks until one exists (headset may still be asleep). */
    public interface SurfaceSource {
        Surface awaitSurface(int width, int height, boolean tenBit) throws InterruptedException;
    }

    /** Receives per-frame events: kind 1 = decoded (handed to the display pipeline), 2 = displayed. */
    interface AckSink {
        void send(int kind, long ptsUs, long aheadNs);
        default void send(int kind, long ptsUs) { send(kind, ptsUs, 0); }
        /** Kind 4: u64 pose id then 21 floats: orientation, position, both eyes' fov angles, and the
         *  head's linear and angular velocity (SteamVR needs those to extrapolate between samples). */
        void sendPose(long id, float[] pose);
        /** Kind 5: u64 pose id, u8 leftActive, u8 rightActive, then 2*26*8 floats of hand joints. */
        void sendHands(long id, float[] hands);
    }

    /** Experiment knob: extra decoder keys as "key=int,key=int", applied to the MediaFormat. */
    public static volatile String vendorKeys = "";

    private final SurfaceSource surfaces;
    private final AtomicBoolean running = new AtomicBoolean(false);
    /** Reused: a fresh array per AU is ~1 MB of garbage at 800 Mbps. */
    private final byte[] accessUnit = new byte[MAX_AU];
    private volatile String status = "Receiver idle";
    private volatile Thread listener;
    private volatile ServerSocket server;
    private volatile Socket client;
    private volatile HandlerThread renderThread;

    public StreamReceiver(SurfaceSource surfaces) {
        if (surfaces == null) throw new IllegalArgumentException("SurfaceSource required");
        this.surfaces = surfaces;
    }

    /** Starts the listener thread and returns immediately. Safe to call twice. */
    public void start() {
        if (!running.compareAndSet(false, true)) return;
        Log.i(TAG, "XRCONFIG vendor=" + vendorKeys);
        Thread thread = new Thread(this::serve, "xrw-receiver");
        thread.setDaemon(true);
        listener = thread;
        thread.start();
    }

    /** Stops listening, drops the current connection and releases the frame-rendered thread. */
    public void stop() {
        running.set(false);
        closeSockets();
        HandlerThread rendered = renderThread;
        renderThread = null;
        if (rendered != null) rendered.quitSafely();
        Thread thread = listener;
        listener = null;
        if (thread != null) thread.interrupt();
        show("Receiver stopped");
    }

    /** One short line for logging or the debug HUD. */
    public String status() { return status; }

    private void serve() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY);
        while (running.get()) {
            try (ServerSocket ss = new ServerSocket(PORT)) {
                server = ss;
                ss.setReuseAddress(true);
                show("Listening on TCP " + PORT);
                try (Socket socket = ss.accept()) {
                    client = socket;
                    socket.setTcpNoDelay(true);
                    decode(socket);
                } finally { client = null; }
            } catch (Exception e) {
                if (running.get()) {
                    Log.e(TAG, "Receiver failed", e);
                    show("Receiver error: " + e.getClass().getSimpleName() + " " + e.getMessage());
                    SystemClock.sleep(1000);
                }
            } finally { server = null; }
        }
    }

    private void decode(Socket socket) throws Exception {
        DataInputStream in = new DataInputStream(new BufferedInputStream(socket.getInputStream(), 1024 * 1024));
        byte[] magic = new byte[4];
        in.readFully(magic);
        // "XRW1" w h fps = AVC; "XRW2" w h fps codec: codec 1 = MV-HEVC (w x h per eye, both views per AU)
        String magicText = new String(magic, StandardCharsets.US_ASCII);
        if (!"XRW1".equals(magicText) && !"XRW2".equals(magicText)) throw new IllegalArgumentException("Bad stream magic");
        int width = in.readInt();
        int height = in.readInt();
        int fps = in.readInt();
        // codec id: 0 = AVC, 1/2 = MV-HEVC 8/10-bit, 3/4 = HEVC 8/10-bit. AVC and HEVC carry both eyes
        // side by side in one frame; MV-HEVC carries one view per layer, so the renderer shows it whole.
        int codecId = "XRW2".equals(magicText) ? in.readInt() : 0;
        boolean mvhevc = codecId == 1 || codecId == 2;
        boolean hevc = codecId == 3 || codecId == 4;
        boolean tenBit = codecId == 2 || codecId == 4;
        NativeXr.setStereoLayout(mvhevc ? 1 : 0);
        if (width < 16 || height < 16 || width > 8192 || height > 8192 || fps < 1 || fps > 240)
            throw new IllegalArgumentException("Bad stream dimensions/rate");

        DataOutputStream ack = new DataOutputStream(socket.getOutputStream());
        AckSink acks = new AckSink() {
            @Override public void send(int kind, long pts, long aheadNs) {
                synchronized (ack) {
                    try {
                        ack.writeByte(kind);
                        ack.writeLong(pts);
                        if (kind == 3) ack.writeLong(aheadNs);   // ns from now until the frame is shown
                        ack.flush();
                    } catch (Exception ignored) { }
                }
            }

            @Override public void sendPose(long id, float[] pose) {
                if (pose == null || pose.length < 22) return;
                synchronized (ack) {
                    try {
                        ack.writeByte(4);
                        ack.writeLong(id);
                        for (int i = 1; i < 22; i++) ack.writeFloat(pose[i]);
                        ack.flush();
                    } catch (Exception ignored) { }
                }
            }

            @Override public void sendHands(long id, float[] hands) {
                if (hands == null || hands.length < 2 + 2 * 26 * 8) return;
                synchronized (ack) {
                    try {
                        ack.writeByte(5);
                        ack.writeLong(id);
                        ack.writeByte(hands[0] != 0f ? 1 : 0);
                        ack.writeByte(hands[1] != 0f ? 1 : 0);
                        for (int i = 2; i < 2 + 2 * 26 * 8; i++) ack.writeFloat(hands[i]);
                        ack.flush();
                    } catch (Exception ignored) { }
                }
            }
        };
        int pendingLength = 0;
        long pendingPts = 0;
        byte[] csd = null;
        if (mvhevc || hevc) {   // HEVC decoders want csd-0 (every layer's VPS/SPS/PPS) from the first keyframe
            do {
                pendingLength = in.readInt();
                pendingPts = in.readLong();
                if (pendingLength <= 0 || pendingLength > MAX_AU) throw new IllegalArgumentException("Bad access-unit length " + pendingLength);
                in.readFully(accessUnit, 0, pendingLength);
            } while (!Hevc.isKeyframe(accessUnit, pendingLength));
            csd = Hevc.parameterSets(accessUnit, pendingLength);
            Log.i(TAG, "MV-HEVC csd-0 " + csd.length + " bytes");
        }
        MediaCodec codec = startDecoder(width, height, fps, acks, mvhevc, hevc, csd, tenBit);
        String codecName = codec.getName();
        show(String.format(Locale.US, "Connected %dx%d@%d %s via %s",
                width, height, fps, mvhevc ? "MV-HEVC" : hevc ? "HEVC" : "AVC", codecName));

        long frames = 0;
        long started = SystemClock.elapsedRealtime();
        Stats stats = new Stats();
        // Output thread: release each decoded frame the moment it is ready and echo its pts to the
        // sender, so the PC can time send -> decoded on its own clock (no cross-device clock sync).
        AtomicBoolean decoding = new AtomicBoolean(true);
        final MediaCodec outCodec = codec;
        Thread outputThread = new Thread(() -> {
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY);
            MediaCodec.BufferInfo out = new MediaCodec.BufferInfo();
            while (decoding.get()) {
                int index;
                try { index = outCodec.dequeueOutputBuffer(out, 2_000); } catch (IllegalStateException stopped) { break; }
                if (index >= 0) {
                    Long received = stats.receivedAt.remove(out.presentationTimeUs);
                    if (received != null) stats.addDecode((System.nanoTime() - received) / 1e6);
                    stats.framesOut++;
                    outCodec.releaseOutputBuffer(index, true);
                    acks.send(1, out.presentationTimeUs);
                } else if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    Log.i(TAG, "Output: " + outCodec.getOutputFormat());
                }
            }
        }, "xrw-output");
        outputThread.start();
        // Kind-3 acks: what the VR runtime says about each submitted frame, so the sender can measure
        // send -> light on the display rather than send -> flat compositor latch.
        Thread photonThread = new Thread(() -> {
            while (decoding.get()) {
                long[] events = NativeXr.drainDisplayEvents();
                for (int i = 0; i + 2 < events.length; i += 3) {
                    acks.send(3, events[i], events[i + 1] - System.nanoTime());
                }
                // 1 ms: any slower and the frame is already on screen before we report it.
                try { Thread.sleep(1); } catch (InterruptedException stop) { return; }
            }
        }, "xrw-photons");
        photonThread.setDaemon(true);
        photonThread.start();
        // Kind-4 records: the head pose the PC should render the next frame for. The driver stamps
        // that frame with the pose's id (as its pts), so we know which pose each image belongs to.
        Thread poseThread = new Thread(() -> {
            long sentId = -1;
            while (decoding.get()) {
                long id = NativeXr.latestPoseId();
                if (id != sentId) {
                    sentId = id;
                    acks.sendPose(id, NativeXr.latestPose());
                    acks.sendHands(id, NativeXr.latestHands());
                }
                try { Thread.sleep(2); } catch (InterruptedException stop) { return; }
            }
        }, "xrw-pose");
        poseThread.setDaemon(true);
        poseThread.start();
        try {
            while (running.get()) {
                int length;
                long ptsUs;
                if (pendingLength > 0) {                       // the keyframe read to build csd-0
                    length = pendingLength;
                    ptsUs = pendingPts;
                    pendingLength = 0;
                } else {
                    try { length = in.readInt(); } catch (EOFException done) { break; }
                    ptsUs = in.readLong();
                    if (length <= 0 || length > MAX_AU) throw new IllegalArgumentException("Bad access-unit length " + length);
                    in.readFully(accessUnit, 0, length);
                }
                long receivedNs = System.nanoTime();
                stats.bytesIn += length;

                int inputIndex;
                long waitStart = System.nanoTime();
                do {
                    inputIndex = codec.dequeueInputBuffer(10_000);
                } while (inputIndex < 0 && running.get());
                if (inputIndex < 0) break;
                stats.inputWaitNs += System.nanoTime() - waitStart;
                stats.receivedAt.put(ptsUs, receivedNs);
                ByteBuffer input = codec.getInputBuffer(inputIndex);
                if (input == null || input.capacity() < length) throw new IllegalStateException("Decoder input buffer too small");
                input.clear();
                input.put(accessUnit, 0, length);
                codec.queueInputBuffer(inputIndex, 0, length, ptsUs, 0);
                stats.maybeLog();
                frames++;
                if (frames % 60 == 0) {
                    double seconds = Math.max(0.001, (SystemClock.elapsedRealtime() - started) / 1000.0);
                    show(String.format(Locale.US, "%dx%d@%d %s: %d AUs (%.1f/s) %s",
                            width, height, fps, codecName, frames, frames / seconds, stats.line()));
                }
            }
        } finally {
            decoding.set(false);
            try { outputThread.join(500); } catch (InterruptedException ignored) { }
            photonThread.interrupt();
            poseThread.interrupt();
            try { codec.stop(); } catch (Exception stopError) {
                Log.w(TAG, "Decoder was already stopped", stopError);
            }
            try { codec.release(); } catch (Exception releaseError) {
                Log.w(TAG, "Decoder release failed", releaseError);
            }
            show("Stream ended, listening for reconnect");
        }
    }

    /** AVC (c2.qti.avc.decoder.low_latency) or MV-HEVC (video/x-mvhevc, csd-0 required) decoder. */
    private MediaCodec startDecoder(int width, int height, int fps, AckSink acks,
                                    boolean mvhevc, boolean hevc, byte[] csd, boolean tenBit) throws Exception {
        String mime = mvhevc ? "video/x-mvhevc" : hevc ? MediaFormat.MIMETYPE_VIDEO_HEVC
                                                       : MediaFormat.MIMETYPE_VIDEO_AVC;
        // The qti "low_latency" decoder variants are the ones that answer within a few ms; MV-HEVC has none.
        String preferred = mvhevc ? null : hevc ? "c2.qti.hevc.decoder.low_latency" : "c2.qti.avc.decoder.low_latency";
        MediaCodec codec = null;
        if (preferred != null) {
            try {
                codec = MediaCodec.createByCodecName(preferred);
            } catch (Exception unavailable) {
                Log.w(TAG, "no " + preferred + "; falling back to the default decoder");
            }
        }
        if (codec == null) codec = MediaCodec.createDecoderByType(mime);
        List<String> supported = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                ? codec.getSupportedVendorParameters() : Collections.emptyList();
        MediaFormat format = MediaFormat.createVideoFormat(mime, width, height);
        format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1);
        format.setInteger(MediaFormat.KEY_PRIORITY, 0);
        format.setInteger(MediaFormat.KEY_OPERATING_RATE, fps);
        format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, MAX_AU);
        // Qualcomm keys ALVR also sets: without them the c2.qti AVC decoder holds ~5 frames (it fills
        // its reorder buffer before output), measured as ~5 frame intervals of decode latency.
        if (csd != null && csd.length > 0) format.setByteBuffer("csd-0", ByteBuffer.wrap(csd));
        // Every key the decoder exposes that we want: the two low-latency ones keep it from filling a
        // reorder buffer (~5 frames on the qti AVC decoder), the fence/early pair cut decode to 3-5 ms,
        // and the stereo trio asks the MV-HEVC decoder for both views.
        String[][] keys = {{"vendor.qti-ext-dec-low-latency.enable", "1"},
                {"vendor.qti-ext-dec-picture-order.enable", "1"},
                {"vendor.qti-ext-output-sw-fence-enable.value", "1"},
                {"vendor.qti-ext-dec-early-notify.value", "1"},
                {"vendor.qti-ext-dec-output-stereo-mode", "1"},
                {"vendor.qti-ext-multi-view-hevc.value", "1"},
                {"vendor.qti-ext-dec-multiview-count", "2"}};
        for (String[] kv : keys) {
            boolean stereoKey = kv[0].contains("stereo") || kv[0].contains("multi");
            if (stereoKey != mvhevc) continue;
            if (supported.isEmpty() || supported.contains(kv[0])) {
                format.setInteger(kv[0], Integer.parseInt(kv[1]));
                Log.i(TAG, "XRCONFIG set " + kv[0] + "=" + kv[1]);
            }
        }
        for (String pair : vendorKeys.split(",")) {
            String[] kv = pair.trim().split("=");
            if (kv.length == 2) {
                format.setInteger(kv[0].trim(), Integer.parseInt(kv[1].trim()));
                Log.i(TAG, "XRCONFIG set " + kv[0].trim() + "=" + kv[1].trim());
            }
        }
        HandlerThread rendered = renderThread;
        if (rendered == null) {
            rendered = new HandlerThread("xrw-rendered");
            rendered.start();
            renderThread = rendered;
        }
        // Actual display time of each frame (SurfaceFlinger latch), reported back as a kind-2 ack.
        codec.setOnFrameRenderedListener((c, ptsUs, renderNs) -> acks.send(2, ptsUs),
                new Handler(rendered.getLooper()));
        show("Waiting for the XR output surface");
        Surface surface = surfaces.awaitSurface(width, height, tenBit);
        if (surface == null || !surface.isValid()) throw new IllegalStateException("No valid output surface");
        codec.configure(format, surface, null, 0);
        codec.start();
        Log.i(TAG, "XRCONFIG format " + format);
        Log.i(TAG, "Started " + codec.getName() + " for " + width + "x" + height + "@" + fps);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            // Every vendor (vendor.qti-ext-*) parameter this decoder exposes, to find hidden modes.
            for (String name : supported) {
                MediaCodec.ParameterDescriptor d = codec.getParameterDescriptor(name);
                Log.i(TAG, "XRVENDOR " + codec.getName() + " " + name + " type=" + (d == null ? "?" : d.getType()));
            }
        }
        return codec;
    }

    private void show(String text) {
        status = text;
        Log.i(TAG, text);
    }

    private void closeSockets() {
        try { Socket c = client; if (c != null) c.close(); } catch (Exception ignored) { }
        try { ServerSocket s = server; if (s != null) s.close(); } catch (Exception ignored) { }
    }
}
