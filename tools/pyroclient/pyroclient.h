// pyroclient: PyroWave decode on the headset, delivered as RGBA8 AHardwareBuffers.
//
// The ALVR client is GLES/EGL and imports a frame the same way it imports MediaCodec's output:
// an AHardwareBuffer wrapped in an EGLImage. This library gives it exactly that for PyroWave.
// It owns a Vulkan device, lends it to PyroWave (the borrowed-device path: nothing is imported or
// exported between us and the codec), decodes into three plain R8 planes, and converts them into
// one of a ring of RGBA8 AHardwareBuffer-backed images. Every call is synchronous: when
// pyroclient_decode returns, the buffer is finished on the GPU and safe to import.
//
// Everything here was proven first in tools/pyrowave_android (55 dB against the PC reference);
// the three silent failure modes recorded there are handled: our own command buffer and submit,
// the queue type matching the decode path, and explicit layout transitions.
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct AHardwareBuffer AHardwareBuffer;
typedef struct pyroclient pyroclient;

typedef struct pyroclient_frame_info {
    double decode_ms;    // GPU time of PyroWave's decode (timestamp queries)
    double convert_ms;   // GPU time of the YCbCr->RGBA pass, i.e. the GLES bridge
    double total_ms;     // wall clock from submit to fence, what the frame actually waited
    int complete;        // 1 if every packet of the frame had arrived before decoding
} pyroclient_frame_info;

// width/height of the coded frame; chroma444 selects 4:4:4 (else 4:2:0); ring is how many output
// buffers rotate (a buffer handed out stays valid until `ring - 1` further decodes). NULL on
// failure, with the reason in logcat under "pyroclient".
// full_range must match what the server encoded (ALVR's video.use_full_range); a mismatch is
// silent and costs ~25 dB.
// wavelet is 97 (CDF 9/7) or 53 (CDF 5/3, compute path only); it must match the stream's sequence
// header or every frame is rejected. Precision comes from the debug.xrwired.pyro_precision property.
pyroclient *pyroclient_create(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring, int wavelet);

// As pyroclient_create, plus the headset decode path the dashboard chose: 0 auto (PyroWave's
// heuristic), 1 fragment, 2 compute. debug.xrwired.decode_path overrides it; see decode_path.h.
pyroclient *pyroclient_create_ex(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring, int wavelet, int decode_path);

// Feed bitstream. A whole frame or one network packet; PyroWave sequences frames itself.
// Returns 1 when the current frame is complete and ready to decode, 0 otherwise, <0 on error.
int pyroclient_push_packet(pyroclient *c, const void *data, size_t size);

// Whether a decode would produce a complete frame (allow_partial=0) or any frame at all (1).
int pyroclient_is_ready(pyroclient *c, int allow_partial);

// Decode the queued frame into the next ring buffer and wait for it. On success *out is the
// AHardwareBuffer to import; it is owned by the library. Returns 0 on success.
int pyroclient_decode(pyroclient *c, AHardwareBuffer **out, pyroclient_frame_info *info);

// Throw away whatever is queued (e.g. a frame whose deadline passed).
void pyroclient_clear(pyroclient *c);

void pyroclient_destroy(pyroclient *c);

#ifdef __cplusplus
}
#endif
