package com.xrwired.receiverxr;

import java.io.ByteArrayOutputStream;
import java.util.Arrays;

/** Plain-JDK test for Hevc (run by tools/tests/test_receiver_xr_protocol.py; the Android build has no JUnit). */
public final class HevcTest {
    /** NAL with a 4- or 3-byte start code: 2-byte header (type, layer id, tid 1) then payload. */
    static byte[] nal(int type, int layer, boolean longStart, int... payload) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        if (longStart) b.write(0);
        b.write(0); b.write(0); b.write(1);
        b.write((type << 1) | (layer >> 5));
        b.write(((layer & 31) << 3) | 1);
        for (int p : payload) b.write(p);
        return b.toByteArray();
    }

    static byte[] cat(byte[]... parts) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        for (byte[] p : parts) b.write(p, 0, p.length);
        return b.toByteArray();
    }

    static byte[] withLongStart(byte[] n) { return n[2] == 1 ? cat(new byte[] {0}, n) : n; }

    public static void main(String[] args) {
        byte[] vps = nal(32, 0, true, 0x0c, 0x01), sps = nal(33, 0, true, 0x01, 0x60), pps = nal(34, 0, false, 0xc1);
        byte[] sei = nal(39, 0, true, 0x05), idr = nal(19, 0, true, 0xaf, 0x00, 0x00, 0x03, 0x01);
        byte[] sps1 = nal(33, 1, false, 0x01), pps1 = nal(34, 1, true, 0xc0), idr1 = nal(19, 1, true, 0xaf);
        byte[] au = cat(vps, sps, pps, sei, idr, sps1, pps1, idr1);
        byte[] padded = Arrays.copyOf(au, au.length + 100);          // decoder buffers are larger than the AU

        // csd-0 keeps both layers' parameter sets, in stream order, each with a 4-byte start code.
        byte[] csd = Hevc.parameterSets(padded, au.length);
        byte[] expected = cat(vps, sps, withLongStart(pps), withLongStart(sps1), pps1);
        assert Arrays.equals(csd, expected) : "csd mismatch: " + Arrays.toString(csd);

        // Nothing past the given length leaks in, even though the buffer is reused and longer.
        byte[] baseOnly = Hevc.parameterSets(padded, cat(vps, sps, pps).length);
        assert Arrays.equals(baseOnly, cat(vps, sps, withLongStart(pps))) : "length bound ignored";

        byte[] pFrame = cat(nal(1, 0, true, 0x9a), nal(1, 1, true, 0x9b));
        assert Hevc.parameterSets(pFrame, pFrame.length).length == 0 : "P frame has no parameter sets";
        assert Hevc.isKeyframe(au, au.length) && !Hevc.isKeyframe(pFrame, pFrame.length) : "keyframe detection";
        // CRA (21) and the other IRAP types count as keyframes; SEI (39) and trailing slices do not.
        byte[] cra = cat(vps, sps, pps, nal(21, 0, true, 0xaf));
        assert Hevc.isKeyframe(cra, cra.length) : "CRA is a keyframe";
        assert !Hevc.isKeyframe(sei, sei.length) : "SEI is not a keyframe";
        System.out.println("PASS");
    }
}
