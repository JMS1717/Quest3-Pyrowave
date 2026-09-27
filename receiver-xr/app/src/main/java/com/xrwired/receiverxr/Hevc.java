package com.xrwired.receiverxr;

import java.io.ByteArrayOutputStream;

/** Annex B HEVC helpers for the MV-HEVC path (no Android dependencies, so it runs under a plain JDK). */
final class Hevc {
    private Hevc() { }

    /** VPS/SPS/PPS NALs of every layer in the access unit, each with a 4-byte start code (csd-0). */
    static byte[] parameterSets(byte[] au, int length) {
        ByteArrayOutputStream csd = new ByteArrayOutputStream();
        int start = nextStart(au, 0, length);
        while (start >= 0) {
            int header = start + 3;
            int next = nextStart(au, header, length);
            int end = next < 0 ? length : (next > 0 && au[next - 1] == 0 ? next - 1 : next);
            int type = header < length ? (au[header] >> 1) & 0x3f : -1;
            if (type >= 32 && type <= 34) {
                csd.write(0);
                csd.write(au, start, end - start);
            }
            start = next;
        }
        return csd.toByteArray();
    }

    /** True when the access unit contains an IRAP picture (NAL types 16-23). */
    static boolean isKeyframe(byte[] au, int length) {
        for (int s = nextStart(au, 0, length); s >= 0; s = nextStart(au, s + 3, length)) {
            int type = s + 3 < length ? (au[s + 3] >> 1) & 0x3f : -1;
            if (type >= 16 && type <= 23) return true;
        }
        return false;
    }

    /** Index of the next 00 00 01 at or after from, or -1. */
    private static int nextStart(byte[] b, int from, int length) {
        for (int i = from; i + 2 < length; i++) {
            if (b[i] == 0 && b[i + 1] == 0 && b[i + 2] == 1) return i;
        }
        return -1;
    }
}
