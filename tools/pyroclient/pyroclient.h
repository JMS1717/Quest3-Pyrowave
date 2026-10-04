// pyroclient: PyroWave decode on the headset, delivered as RGBA8 AHardwareBuffers.
//
// The ALVR client is GLES/EGL and imports a frame the same way it imports MediaCodec's output:
// an AHardwareBuffer wrapped in an EGLImage. This library gives it exactly that for PyroWave.
// It owns a Vulkan device, lends it to PyroWave (the borrowed-device path: nothing is imported or
// exported between us and the codec), decodes into three plain R8 planes, and converts them into
// one of a ring of RGBA8 AHardwareBuffer-backed images. Legacy decode is synchronous: when
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
    double total_ms;     // wall clock from command recording start through fence/query completion
    int complete;        // 1 if every packet of the frame had arrived before decoding
    double record_ms;    // CPU wall time preparing commands, before vkQueueSubmit
    double wait_ms;      // CPU wall time in vkQueueSubmit + vkWaitForFences (includes scheduling)
} pyroclient_frame_info;

// width/height of the coded frame; chroma444 selects 4:4:4 (else 4:2:0); ring is how many output
// buffers rotate (a buffer handed out stays valid until `ring - 1` further decodes). NULL on
// failure, with the reason in logcat under "pyroclient".
// full_range must match what the server encoded (ALVR's video.use_full_range); a mismatch is
// silent and costs ~25 dB.
// wavelet is 97 (CDF 9/7), 53 (CDF 5/3), or 2 (Haar); 53/2 require Compute; it must match the stream's sequence
// header or every frame is rejected. Precision comes from the debug.xrwired.pyro_precision property.
pyroclient *pyroclient_create(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring, int wavelet);

// As pyroclient_create, plus the headset decode path the dashboard chose: 0 auto (PyroWave's
// heuristic), 1 fragment, 2 compute. debug.xrwired.decode_path overrides it; see decode_path.h.
pyroclient *pyroclient_create_ex(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring, int wavelet, int decode_path);

// As pyroclient_create_ex; low_queue_priority=1 asks for a LOW global-priority decode queue
// (VK_KHR/EXT_global_priority) so higher-priority GLES work preempts decode. It falls back to
// the default priority when unsupported or rejected; logcat reports [Q3PW_PRIORITY].
pyroclient *pyroclient_create_prioritized(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring,
                                          int wavelet, int decode_path, int low_queue_priority);

// Feed bitstream. A whole frame or one network packet; PyroWave sequences frames itself.
// Returns 1 when the current frame is complete and ready to decode, 0 otherwise, <0 on error.
int pyroclient_push_packet(pyroclient *c, const void *data, size_t size);

// Whether a decode would produce a complete frame (allow_partial=0) or any frame at all (1).
int pyroclient_is_ready(pyroclient *c, int allow_partial);

// Decode the queued frame into the next ring buffer and wait for it. On success *out is the
// AHardwareBuffer to import; it is owned by the library. Returns 0 on success.
int pyroclient_decode(pyroclient *c, AHardwareBuffer **out, pyroclient_frame_info *info);
// Streaming caller excludes the leased render buffer and the pending buffer that
// could be dequeued while GPU work runs. Ring size must leave at least one free slot.
int pyroclient_decode_guarded(pyroclient *c, AHardwareBuffer **out, pyroclient_frame_info *info,
                             AHardwareBuffer *protected_a, AHardwareBuffer *protected_b);

// Experimental early publication (debug.q3pw.ready_fd=1). At most ONE submission
// may be outstanding. ready_fd >= 0 is transferred to caller and MUST gate all
// GLES reads. info is provisional until finish_pending succeeds. ready_fd == -1
// means a completed synchronous fallback. Before pushing/clearing/decoding another
// frame, call finish_pending; no codec/upload/command resources overlap.
int pyroclient_submit_guarded(pyroclient *c, AHardwareBuffer **out, pyroclient_frame_info *info,
                             AHardwareBuffer *protected_a, AHardwareBuffer *protected_b, int *ready_fd);
int pyroclient_finish_pending(pyroclient *c, pyroclient_frame_info *info);

// Optional GLES release-fence handoff. Decode remains synchronous. Nonzero token
// identifies a completed frame whose native context supports SYNC_FD import.
uint64_t pyroclient_output_release_token(AHardwareBuffer *buffer);
// Always consumes fd (including rejection). Return 1 only when registered to the
// matching frame; caller must synchronously finish GLES before dropping a rejected lease.
int pyroclient_attach_release_fd(AHardwareBuffer *buffer, uint64_t token, int fd);

// Throw away whatever is queued (e.g. a frame whose deadline passed).
void pyroclient_clear(pyroclient *c);

void pyroclient_destroy(pyroclient *c);

#ifdef __cplusplus
}
#endif
