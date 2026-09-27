package com.xrwired.receiver;

import android.media.MediaCodec;
import android.os.Process;
import android.util.Log;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.util.Iterator;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * UDP transport on port 45101. Datagrams (big-endian):
 *   hello:  "XRH1" u32 width u32 height u32 fps          (sender repeats it until the first ack)
 *   chunk:  "XRU1" u64 pts u32 frameLen u32 offset u16 chunkLen u16 flags, then chunkLen bytes
 * A frame is decoded once all of its bytes arrived. Any frame still incomplete when a newer frame
 * completes is dropped (lost datagram); decoding resumes cleanly at the next keyframe. Each decoded
 * frame's pts is echoed to the sender as "XRA1" u64 pts, so the PC times send -> decoded on one clock.
 */
final class UdpPath {
    static final int PORT = 45101;
    private static final int HEADER = 24;
    private final MainActivity activity;
    private volatile DatagramSocket socket;

    UdpPath(MainActivity activity) { this.activity = activity; }

    void close() { DatagramSocket s = socket; if (s != null) s.close(); }

    private static final class Assembly {
        final byte[] data; int received;
        Assembly(int length) { data = new byte[length]; }
    }

    void serve() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY);
        while (activity.running.get()) {
            try (DatagramSocket s = new DatagramSocket(null)) {
                socket = s;
                s.setReuseAddress(true);
                s.setReceiveBufferSize(16 * 1024 * 1024);
                s.bind(new InetSocketAddress(PORT));
                Log.i(MainActivity.TAG, "UDP listening on " + PORT + " (recv buffer " + s.getReceiveBufferSize() + ")");
                session(s);
            } catch (Exception e) {
                if (activity.running.get()) Log.e(MainActivity.TAG, "UDP receiver failed", e);
            }
        }
    }

    private void session(DatagramSocket s) throws Exception {
        byte[] buf = new byte[65536];
        DatagramPacket packet = new DatagramPacket(buf, buf.length);
        int width, height, fps;
        SocketAddress sender;
        while (true) {                                       // wait for hello
            s.setSoTimeout(0);
            s.receive(packet);
            ByteBuffer b = ByteBuffer.wrap(buf, 0, packet.getLength());
            if (packet.getLength() >= 16 && b.getInt() == 0x58524831) {   // "XRH1"
                width = b.getInt(); height = b.getInt(); fps = b.getInt();
                sender = packet.getSocketAddress();
                break;
            }
        }
        final SocketAddress ackTo = sender;
        MainActivity.AckSink acks = (kind, pts) -> {
            byte[] a = new byte[13];
            ByteBuffer.wrap(a).putInt(0x58524131).put((byte) kind).putLong(pts);   // "XRA1"
            try { s.send(new DatagramPacket(a, 13, ackTo)); } catch (Exception ignored) { }
        };
        MediaCodec codec = activity.startDecoder(width, height, fps, acks);
        activity.show(String.format(Locale.US, "UDP %dx%d @ %d\nDecoder: %s", width, height, fps, codec.getName()));
        MainActivity.Stats stats = new MainActivity.Stats();
        AtomicBoolean decoding = new AtomicBoolean(true);
        Thread output = new Thread(() -> {
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            while (decoding.get()) {
                int index;
                try { index = codec.dequeueOutputBuffer(info, 2_000); } catch (IllegalStateException stopped) { break; }
                if (index >= 0) {
                    Long received = stats.receivedAt.remove(info.presentationTimeUs);
                    if (received != null) stats.addDecode((System.nanoTime() - received) / 1e6);
                    stats.framesOut++;
                    codec.releaseOutputBuffer(index, true);
                    acks.send(1, info.presentationTimeUs);
                }
            }
        }, "xrw-udp-output");
        output.start();

        TreeMap<Long, Assembly> pending = new TreeMap<>();
        long dropped = 0, lastLog = System.nanoTime();
        try {
            s.setSoTimeout(3000);
            while (activity.running.get()) {
                try { s.receive(packet); } catch (SocketTimeoutException idle) { break; }
                int n = packet.getLength();
                ByteBuffer b = ByteBuffer.wrap(buf, 0, n);
                if (n < HEADER || b.getInt() != 0x58525531) continue;                  // "XRU1"
                long pts = b.getLong();
                int frameLen = b.getInt(), offset = b.getInt(), chunkLen = b.getShort() & 0xFFFF;
                b.getShort();                                                          // flags
                if (frameLen <= 0 || frameLen > MainActivity.MAX_AU || offset + chunkLen > frameLen
                        || HEADER + chunkLen > n) continue;
                stats.bytesIn += chunkLen;
                Assembly a = pending.get(pts);
                if (a == null) { a = new Assembly(frameLen); pending.put(pts, a); }
                System.arraycopy(buf, HEADER, a.data, offset, chunkLen);
                a.received += chunkLen;
                if (a.received < frameLen) continue;

                // complete: anything older is a frame that lost a datagram
                Iterator<Map.Entry<Long, Assembly>> it = pending.headMap(pts, false).entrySet().iterator();
                while (it.hasNext()) { it.next(); it.remove(); dropped++; }
                pending.remove(pts);
                long receivedNs = System.nanoTime();
                int inputIndex;
                long waitStart = System.nanoTime();
                do { inputIndex = codec.dequeueInputBuffer(10_000); } while (inputIndex < 0 && activity.running.get());
                if (inputIndex < 0) break;
                stats.inputWaitNs += System.nanoTime() - waitStart;
                ByteBuffer input = codec.getInputBuffer(inputIndex);
                input.clear();
                input.put(a.data, 0, frameLen);
                stats.receivedAt.put(pts, receivedNs);
                codec.queueInputBuffer(inputIndex, 0, frameLen, pts, 0);
                stats.maybeLog();
                if (System.nanoTime() - lastLog > 1_000_000_000L) {
                    Log.i(MainActivity.TAG, "XRUDP dropped_frames=" + dropped + " pending=" + pending.size());
                    lastLog = System.nanoTime();
                }
            }
        } finally {
            decoding.set(false);
            try { output.join(500); } catch (InterruptedException ignored) { }
            try { codec.stop(); } catch (Exception ignored) { }
            try { codec.release(); } catch (Exception ignored) { }
            Log.i(MainActivity.TAG, "XRUDP session ended, dropped_frames=" + dropped);
        }
    }
}
