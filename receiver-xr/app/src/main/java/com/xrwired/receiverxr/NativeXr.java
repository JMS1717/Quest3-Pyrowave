package com.xrwired.receiverxr;

/** JNI bridge to the native OpenXR client (libxrwired_xr.so). */
public final class NativeXr {
    static { System.loadLibrary("xrwired_xr"); }

    /** Creates the instance/session/swapchains and runs the frame loop on the calling thread until stop().
     *  The activity is needed by the Android OpenXR loader and by XR_KHR_android_create_instance. */
    public static native boolean start(Object activity);
    public static native void stop();

    public static native android.view.Surface createDecoderSurface(int width, int height, boolean tenBit);
    public static native void releaseDecoderSurface();

    public static native void setStereoLayout(int layout);

    /** Log XRPIX colour samples of what is about to be displayed (measurement runs). */
    public static native void setPixelProbe(boolean on);

    /** Ask for a 10-bit linear swapchain instead of the default sRGB one. Call before start(). */
    public static native void setTenBit(boolean tenBit);

    /** Hold each frame back until this many ms before its display time (0 = submit immediately). */
    public static native void setSubmitMargin(int ms);

    /** Request a display refresh rate; returns the rate actually in use (0 if unsupported). */
    public static native float setDisplayRate(float hz);   // 0 = side-by-side, 1 = single view

    /** Latest head pose: [id, qx,qy,qz,qw, px,py,pz, left fov l,r,u,d, right fov l,r,u,d,
     *  linear velocity xyz, angular velocity xyz]. */
    public static native float[] latestPose();

    /** The same pose's id at full precision. */
    public static native long latestPoseId();

    /** Latest hand joints: [leftActive, rightActive, left 26*8 floats, right 26*8 floats]. */
    public static native float[] latestHands();

    /** Submitted frames as flat triples (pts_us, predicted display time ns, submit time ns). */
    public static native long[] drainDisplayEvents();

    public static native String stats();
}
