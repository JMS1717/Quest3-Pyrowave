package com.xrwired.receiverxr;

import android.util.Log;

import java.util.Arrays;
import java.util.Locale;
import java.util.concurrent.ConcurrentHashMap;

/** One XRSTAT log line per second: output fps, input Mbps, decode latency (AU fully received to
 *  decoded output available), time blocked waiting for a decoder input buffer, AUs in flight. */
final class Stats {
    final ConcurrentHashMap<Long, Long> receivedAt = new ConcurrentHashMap<>();
    final double[] decodeMs = new double[1024];
    int decodeCount;
    volatile long framesOut;
    long bytesIn, inputWaitNs;
    long windowStart = System.nanoTime();
    /** The last XRSTAT line, so the HUD can show what the log shows. */
    private volatile String line = "";

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
        line = String.format(Locale.US,
                "XRSTAT out_fps=%.1f in_mbps=%.1f dec_p50=%.2f dec_p95=%.2f dec_max=%.2f in_wait_ms=%.1f in_flight=%d",
                framesOut / seconds, bytesIn * 8 / seconds / 1e6, p50, p95, max,
                inputWaitNs / 1e6 / Math.max(1, framesOut), receivedAt.size());
        Log.i(StreamReceiver.TAG, line);
        decodeCount = 0; framesOut = 0; bytesIn = 0; inputWaitNs = 0; windowStart = now;
    }

    String line() { return line; }
}
