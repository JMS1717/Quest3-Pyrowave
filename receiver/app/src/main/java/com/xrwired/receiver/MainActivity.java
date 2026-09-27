package com.xrwired.receiver;

import android.app.Activity;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Process;
import android.graphics.Color;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Log;
import android.view.Gravity;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.TextView;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.ConcurrentHashMap;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

public final class MainActivity extends Activity implements SurfaceHolder.Callback {
    static final String TAG = "XRWired";
    private static final int PORT = 45100;
    static final int MAX_AU = 16 * 1024 * 1024;
    final AtomicBoolean running = new AtomicBoolean(false);
    private TextView status;
    private Thread receiverThread;
    private UdpPath udpPath;
    private WifiManager.WifiLock wifiLock;
    /** Experiment knobs from launch extras: --es vendor "key=int,key=int"  --ez slice_input true */
    volatile String vendorKeys = "";
    volatile boolean sliceInput = false;
    /** 0 = BUFFER_FLAG_PARTIAL_FRAME on non-last slices, 1 = no flags, 2 = no flags + end-of-picture param */
    volatile int sliceVariant = 0;
    /** Receives per-frame events: kind 1 = decoded (handed to the display pipeline), 2 = displayed. */
    interface AckSink { void send(int kind, long ptsUs); }
    private HandlerThread renderThread;
    private volatile Surface surface;
    private volatile ServerSocket server;
    private volatile Socket client;
    private final Object surfaceLock = new Object();

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        if (getIntent() != null) {
            String v = getIntent().getStringExtra("vendor");
            vendorKeys = v == null ? "" : v;
            sliceInput = getIntent().getBooleanExtra("slice_input", false);
            sliceVariant = getIntent().getIntExtra("slice_variant", 0);
        }
        Log.i(TAG, "XRCONFIG vendor=" + vendorKeys + " slice_input=" + sliceInput + " slice_variant=" + sliceVariant);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION |
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN |
                View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        SurfaceView video = new SurfaceView(this);
        video.getHolder().addCallback(this);
        root.addView(video, new FrameLayout.LayoutParams(-1, -1));
        status = new TextView(this);
        status.setTextColor(Color.WHITE);
        status.setBackgroundColor(0x99000000);
        status.setTextSize(16);
        status.setPadding(24, 16, 24, 16);
        FrameLayout.LayoutParams label = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.LEFT);
        label.setMargins(24, 24, 24, 24);
        root.addView(status, label);
        setContentView(root);
        show("Waiting for display surface…");
        // FastConnect 7800 low-latency mode (what ALVR requests too): no Wi-Fi power save while streaming.
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        if (wifi != null) {
            wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "xrwired-receiver");
            wifiLock.acquire();
            Log.i(TAG, "Wi-Fi low-latency lock held=" + wifiLock.isHeld());
        }
    }

    @Override public void surfaceCreated(SurfaceHolder holder) {
        synchronized (surfaceLock) {
            surface = holder.getSurface();
            surfaceLock.notifyAll();
        }
        if (running.compareAndSet(false, true)) {
            receiverThread = new Thread(this::serve, "xr-wired-receiver");
            receiverThread.start();
            udpPath = new UdpPath(this);
            new Thread(udpPath::serve, "xr-wired-udp").start();
        }
    }

    @Override public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
        surface = holder.getSurface();
    }

    @Override public void surfaceDestroyed(SurfaceHolder holder) {
        synchronized (surfaceLock) { surface = null; }
    }

    @Override protected void onDestroy() {
        running.set(false);
        closeSockets();
        if (udpPath != null) udpPath.close();
        if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
        if (renderThread != null) renderThread.quitSafely();
        super.onDestroy();
    }

    private void serve() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY);
        while (running.get()) {
            try (ServerSocket ss = new ServerSocket(PORT)) {
                server = ss;
                ss.setReuseAddress(true);
                show("Listening on TCP " + PORT + "\nRun tools/send_video.py on the Mac");
                try (Socket socket = ss.accept()) {
                    client = socket;
                    socket.setTcpNoDelay(true);
                    decode(socket);
                } finally { client = null; }
            } catch (Exception e) {
                if (running.get()) {
                    Log.e(TAG, "Receiver failed", e);
                    show("Receiver error: " + e.getClass().getSimpleName() + "\n" + e.getMessage());
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
        boolean mvhevc = "XRW2".equals(magicText) && in.readInt() == 1;
        if (width < 16 || height < 16 || width > 8192 || height > 8192 || fps < 1 || fps > 240)
            throw new IllegalArgumentException("Bad stream dimensions/rate");

        DataOutputStream ack = new DataOutputStream(socket.getOutputStream());
        AckSink acks = (kind, pts) -> {
            synchronized (ack) {
                try { ack.writeByte(kind); ack.writeLong(pts); ack.flush(); } catch (Exception ignored) { }
            }
        };
        byte[] accessUnit = new byte[MAX_AU];   // reused: a fresh array per AU is ~1 MB of garbage at 800 Mbps
        int pendingLength = 0;
        long pendingPts = 0;
        byte[] csd = null;
        if (mvhevc) {   // the MV-HEVC decoder wants csd-0 (both layers' VPS/SPS/PPS) from the first keyframe
            do {
                pendingLength = in.readInt();
                pendingPts = in.readLong();
                if (pendingLength <= 0 || pendingLength > MAX_AU) throw new IllegalArgumentException("Bad access-unit length " + pendingLength);
                in.readFully(accessUnit, 0, pendingLength);
            } while (!Hevc.isKeyframe(accessUnit, pendingLength));
            csd = Hevc.parameterSets(accessUnit, pendingLength);
            Log.i(TAG, "MV-HEVC csd-0 " + csd.length + " bytes");
        }
        MediaCodec codec = startDecoder(width, height, fps, acks, mvhevc, csd);
        String codecName = codec.getName();
        show(String.format(Locale.US, "Connected: %dx%d @ %d\nDecoder: %s", width, height, fps, codecName));
        Log.i(TAG, "Started " + codecName + " for " + width + "x" + height + "@" + fps);

        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
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
                if (sliceInput) {
                    queueSlices(codec, inputIndex, accessUnit, length, ptsUs, sliceVariant);
                } else {
                    ByteBuffer input = codec.getInputBuffer(inputIndex);
                    if (input == null || input.capacity() < length) throw new IllegalStateException("Decoder input buffer too small");
                    input.clear();
                    input.put(accessUnit, 0, length);
                    codec.queueInputBuffer(inputIndex, 0, length, ptsUs, 0);
                }
                stats.maybeLog();
                frames++;
                if (frames % 60 == 0) {
                    double seconds = Math.max(0.001, (SystemClock.elapsedRealtime() - started) / 1000.0);
                    show(String.format(Locale.US, "Connected: %dx%d @ %d\nDecoder: %s\nReceived: %d AUs (%.1f/s)",
                            width, height, fps, codecName, frames, frames / seconds));
                }
            }
        } finally {
            decoding.set(false);
            try { outputThread.join(500); } catch (InterruptedException ignored) { }
            try { codec.stop(); } catch (Exception stopError) {
                Log.w(TAG, "Decoder was already stopped", stopError);
            }
            try { codec.release(); } catch (Exception releaseError) {
                Log.w(TAG, "Decoder release failed", releaseError);
            }
            show("Stream ended. Listening for reconnect…");
        }
    }

    /** One XRSTAT log line per second: output fps, input Mbps, decode latency (AU fully received to
     *  decoded output available), time blocked waiting for a decoder input buffer, AUs in flight. */
    static final class Stats {
        final ConcurrentHashMap<Long, Long> receivedAt = new ConcurrentHashMap<>();
        final double[] decodeMs = new double[1024];
        int decodeCount;
        volatile long framesOut;
        long bytesIn, inputWaitNs;
        long windowStart = System.nanoTime();

        synchronized void addDecode(double ms) { if (decodeCount < decodeMs.length) decodeMs[decodeCount++] = ms; }

        synchronized void maybeLog() {
            long now = System.nanoTime();
            double seconds = (now - windowStart) / 1e9;
            if (seconds < 1.0) return;
            double[] d = Arrays.copyOf(decodeMs, decodeCount);
            Arrays.sort(d);
            double p50 = d.length > 0 ? d[d.length / 2] : -1;
            double p95 = d.length > 0 ? d[Math.min(d.length - 1, (int) (d.length * 0.95))] : -1;
            double max = d.length > 0 ? d[d.length - 1] : -1;
            Log.i(TAG, String.format(Locale.US,
                    "XRSTAT out_fps=%.1f in_mbps=%.1f dec_p50=%.2f dec_p95=%.2f dec_max=%.2f in_wait_ms=%.1f in_flight=%d",
                    framesOut / seconds, bytesIn * 8 / seconds / 1e6, p50, p95, max,
                    inputWaitNs / 1e6 / Math.max(1, framesOut), receivedAt.size()));
            decodeCount = 0; framesOut = 0; bytesIn = 0; inputWaitNs = 0; windowStart = now;
        }
    }

    /** Slice-delivery experiment: queue each coded-slice NAL (with the parameter sets / AUD that
     *  precede the first one) as its own input buffer, BUFFER_FLAG_PARTIAL_FRAME on all but the last,
     *  so the decoder can start on slice 0 while later slices are still being copied in. */
    static void queueSlices(MediaCodec codec, int firstIndex, byte[] au, int length, long ptsUs, int variant) {
        java.util.ArrayList<Integer> cuts = new java.util.ArrayList<>();
        for (int i = 0; i + 3 < length; i++) {
            if (au[i] == 0 && au[i + 1] == 0 && au[i + 2] == 1) {
                int type = au[i + 3] & 0x1f;
                if (type == 1 || type == 5) cuts.add(i > 0 && au[i - 1] == 0 ? i - 1 : i);
                i += 2;
            }
        }
        if (cuts.size() <= 1) {
            ByteBuffer input = codec.getInputBuffer(firstIndex);
            input.clear();
            input.put(au, 0, length);
            codec.queueInputBuffer(firstIndex, 0, length, ptsUs, 0);
            return;
        }
        int start = 0;
        int index = firstIndex;
        for (int k = 1; k <= cuts.size(); k++) {
            int end = k < cuts.size() ? cuts.get(k) : length;
            ByteBuffer input = codec.getInputBuffer(index);
            input.clear();
            input.put(au, start, end - start);
            boolean last = k == cuts.size();
            if (last && variant == 2) {
                Bundle eop = new Bundle();
                eop.putInt("vendor.qti-ext-dec-end-of-picture.value", 1);
                codec.setParameters(eop);
            }
            int flags = (variant == 0 && !last) ? MediaCodec.BUFFER_FLAG_PARTIAL_FRAME : 0;
            codec.queueInputBuffer(index, 0, end - start, ptsUs, flags);
            start = end;
            if (!last) {
                do { index = codec.dequeueInputBuffer(10_000); } while (index < 0);
            }
        }
    }

    /** Low-latency AVC decoder on the display surface (shared by the TCP and UDP paths). */
    MediaCodec startDecoder(int width, int height, int fps, AckSink acks) throws Exception {
        return startDecoder(width, height, fps, acks, false, null);
    }

    /** AVC (c2.qti.avc.decoder.low_latency) or MV-HEVC (video/x-mvhevc, csd-0 required) decoder. */
    MediaCodec startDecoder(int width, int height, int fps, AckSink acks, boolean mvhevc, byte[] csd) throws Exception {
        String mime = mvhevc ? "video/x-mvhevc" : MediaFormat.MIMETYPE_VIDEO_AVC;
        MediaCodec codec;
        if (mvhevc) {
            codec = MediaCodec.createDecoderByType(mime);
        } else {
            try {
                codec = MediaCodec.createByCodecName("c2.qti.avc.decoder.low_latency");
            } catch (Exception unavailable) {
                codec = MediaCodec.createDecoderByType(mime);
            }
        }
        java.util.List<String> supported = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                ? codec.getSupportedVendorParameters() : java.util.Collections.emptyList();
        MediaFormat format = MediaFormat.createVideoFormat(mime, width, height);
        format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1);
        format.setInteger(MediaFormat.KEY_PRIORITY, 0);
        format.setInteger(MediaFormat.KEY_OPERATING_RATE, fps);
        format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, MAX_AU);
        // Qualcomm keys ALVR also sets: without them the c2.qti AVC decoder holds ~5 frames (it fills
        // its reorder buffer before output), measured as ~5 frame intervals of decode latency.
        if (mvhevc) {
            format.setByteBuffer("csd-0", ByteBuffer.wrap(csd));
            // ask for stereo (both views) output, only with keys the decoder exposes
            String[][] keys = {{"vendor.qti-ext-dec-low-latency.enable", "1"}, {"vendor.qti-ext-dec-picture-order.enable", "1"},
                    {"vendor.qti-ext-dec-output-stereo-mode", "1"}, {"vendor.qti-ext-multi-view-hevc.value", "1"},
                    {"vendor.qti-ext-dec-multiview-count", "2"}};
            for (String[] kv : keys) {
                if (supported.contains(kv[0])) {
                    format.setInteger(kv[0], Integer.parseInt(kv[1]));
                    Log.i(TAG, "XRCONFIG set " + kv[0] + "=" + kv[1]);
                }
            }
        } else {
            format.setInteger("vendor.qti-ext-dec-low-latency.enable", 1);
            format.setInteger("vendor.qti-ext-dec-picture-order.enable", 1);
        }
        for (String pair : vendorKeys.split(",")) {
            String[] kv = pair.trim().split("=");
            if (kv.length == 2) {
                format.setInteger(kv[0].trim(), Integer.parseInt(kv[1].trim()));
                Log.i(TAG, "XRCONFIG set " + kv[0].trim() + "=" + kv[1].trim());
            }
        }
        if (renderThread == null) { renderThread = new HandlerThread("xrw-rendered"); renderThread.start(); }
        // Actual display time of each frame (SurfaceFlinger latch), reported back as a kind-2 ack.
        codec.setOnFrameRenderedListener((c, ptsUs, renderNs) -> acks.send(2, ptsUs),
                new Handler(renderThread.getLooper()));
        codec.configure(format, waitForSurface(), null, 0);
        codec.start();
        Log.i(TAG, "XRCONFIG format " + format);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            // Every vendor (vendor.qti-ext-*) parameter this decoder exposes, to find hidden modes.
            for (String name : supported) {
                MediaCodec.ParameterDescriptor d = codec.getParameterDescriptor(name);
                Log.i(TAG, "XRVENDOR " + codec.getName() + " " + name + " type=" + (d == null ? "?" : d.getType()));
            }
        }
        return codec;
    }

    Surface waitForSurface() throws InterruptedException {
        synchronized (surfaceLock) {
            while (running.get() && (surface == null || !surface.isValid())) {
                show("Stream connected. Put on/wake the headset…");
                surfaceLock.wait(1000);
            }
            if (!running.get()) throw new InterruptedException("Receiver stopped");
            return surface;
        }
    }

    void show(String text) {
        Log.i(TAG, text.replace('\n', ' '));
        runOnUiThread(() -> status.setText(text));
    }

    private void closeSockets() {
        try { if (client != null) client.close(); } catch (Exception ignored) { }
        try { if (server != null) server.close(); } catch (Exception ignored) { }
    }
}
