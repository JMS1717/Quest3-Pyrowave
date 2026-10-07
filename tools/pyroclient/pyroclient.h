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

// Creation-only Android Surface/Vulkan WSI diagnostic. Requires valid borrowed
// JavaVM and Surface jobject. Retains/releases its own JNI/window references,
// destroys all Vulkan objects before return; never acquires/submits/presents.
int pyroclient_probe_android_surface(void *java_vm, void *java_surface);
// Default-off one-shot Surface chart. Render thread owns every call. Requires
// EXT presentation fences; destroy drains render AND presentation before free.
// Call present only while OpenXR is VISIBLE/FOCUSED. No decoder resources shared.
void *pyroclient_create_surface_chart(void *java_vm, void *java_surface, int *status);
int pyroclient_present_surface_chart(void *chart);
int pyroclient_poll_surface_chart(void *chart);
void pyroclient_destroy_surface_chart(void *chart);

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
// As above with a third excluded buffer: the frame hold (debug.q3pw.frame_hold_us) keeps an older
// decoded frame for the next display period beside the pending one. Needs a ring of four.
int pyroclient_decode_guarded3(pyroclient *c, AHardwareBuffer **out, pyroclient_frame_info *info,
                              AHardwareBuffer *protected_a, AHardwareBuffer *protected_b,
                              AHardwareBuffer *protected_c);

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

// Default-OFF producer pre-record prototype. Explicit single-worker ALVR mode only.
// Exclusive creating worker; warm all THREE actual output slots before enable.
// Requires Haar/compute/420, 3 slots, PYROWAVE_NO_LINEAR_TEX=1, ready/release/stage
// probes off. Geometry frozen by decoder. Granite stage timestamps disabled for
// BOTH control and preparation; native GPU/completion queries remain enabled.
int pyroclient_prerecord_enable(pyroclient *c);
// start/submit return reserved, UNFINISHED output. Do not read/publish before
// finish_pending succeeds with query collection. Only one GPU submission exists.
int pyroclient_prerecord_start(pyroclient *c, const void *data, size_t size,
                              AHardwareBuffer **out, pyroclient_frame_info *info,
                              AHardwareBuffer *protected_a, AHardwareBuffer *protected_b);
// 0 pending, 1 fence signaled (still requires finish_pending), negative failure.
int pyroclient_prerecord_pending_status(pyroclient *c);
// prepare: 1 recorded, 0 context not ready, -4 no free output, -7 upload growth
// deferred until the earlier GPU submission completes; otherwise error.
// Snapshot must protect every possible consumer output. Caller MUST choose latest
// packet and cancel a superseded prepared frame; no FIFO growth permitted.
int pyroclient_prerecord_prepare(pyroclient *c, const void *data, size_t size,
                                AHardwareBuffer *protected_a, AHardwareBuffer *protected_b,
                                uint64_t *generation);
int pyroclient_prerecord_cancel(pyroclient *c, uint64_t generation);
int pyroclient_prerecord_submit(pyroclient *c, uint64_t generation,
                               AHardwareBuffer **out, pyroclient_frame_info *info);
// Preparation-to-submit delay. frame_info.total_ms INCLUDES this delay; record_ms
// excludes it; wait_ms begins at actual submission. Never subtract to claim MTP.
double pyroclient_prerecord_queue_ms(pyroclient *c);

void pyroclient_destroy(pyroclient *c);

#ifdef __cplusplus
}
#endif
