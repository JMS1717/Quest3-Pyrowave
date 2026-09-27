package com.xrwired.receiverxr;

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;
import android.view.Surface;
import android.view.WindowManager;

/**
 * Owns the activity lifecycle for the immersive OpenXR client.
 *
 * <p>The native side ({@link NativeXr}) runs the OpenXR frame loop on its own thread and owns the
 * decoder output surface; the Java side just feeds it video via {@link StreamReceiver}.
 */
public final class MainActivity extends Activity implements StreamReceiver.SurfaceSource {
    private static final String TAG = "XRWiredXR";
    /** How long to wait between polls while the native session is still coming up. */
    private static final long SURFACE_POLL_MS = 50L;

    private StreamReceiver receiver;
    private Thread xrThread;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // Hand tracking is a dangerous permission on Android XR; request it (also grantable via adb).
        try {
            if (checkSelfPermission("android.permission.HAND_TRACKING") != android.content.pm.PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[]{"android.permission.HAND_TRACKING"}, 1);
        } catch (Exception ignored) { }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        // --ez ten_bit true swaps the sRGB swapchain for a 10-bit linear one (banding/HDR tests).
        NativeXr.setPixelProbe(getIntent() != null && getIntent().getBooleanExtra("pixel_probe", false));
        NativeXr.setTenBit(getIntent() != null && getIntent().getBooleanExtra("ten_bit", false));
        // The frame loop blocks its thread for the lifetime of the session, so it gets its own.
        xrThread = new Thread(() -> {
            boolean started = NativeXr.start(this);
            Log.i(TAG, "native XR session ended (started=" + started + ")");
        }, "xrw-xr");
        xrThread.start();

        // Experiment knobs: --ei submit_margin <ms> (late-latch), --ef display_hz <rate>,
        // --es vendor "key=int,..." (decoder vendor parameters).
        int margin = getIntent() != null ? getIntent().getIntExtra("submit_margin", 0) : 0;
        float hz = getIntent() != null ? getIntent().getFloatExtra("display_hz", 0f) : 0f;
        String vendor = getIntent() != null ? getIntent().getStringExtra("vendor") : null;
        if (vendor != null) StreamReceiver.vendorKeys = vendor;
        NativeXr.setSubmitMargin(margin);
        Log.i(TAG, "XRCONFIG submit_margin=" + margin + " display_hz=" + hz + " vendor=" + vendor);
        if (hz > 0f) {                      // the session has to exist first, so ask once it is up
            new Thread(() -> {
                try { Thread.sleep(2500); } catch (InterruptedException stop) { return; }
                Log.i(TAG, "XRCONFIG display rate now " + NativeXr.setDisplayRate(hz) + " Hz");
            }, "xrw-rate").start();
        }

        receiver = new StreamReceiver(this);
        receiver.start();
    }

    @Override
    protected void onDestroy() {
        if (receiver != null) {
            receiver.stop();
            receiver = null;
        }
        NativeXr.releaseDecoderSurface();
        NativeXr.stop();
        Thread thread = xrThread;
        xrThread = null;
        if (thread != null) thread.interrupt();
        super.onDestroy();
    }

    /**
     * Hands the receiver the native decoder surface, waiting for the OpenXR session to publish one.
     */
    @Override
    public Surface awaitSurface(int width, int height, boolean tenBit) throws InterruptedException {
        while (true) {
            Surface surface = NativeXr.createDecoderSurface(width, height, tenBit);
            if (surface != null) return surface;
            Thread.sleep(SURFACE_POLL_MS);
        }
    }
}
