// Diagnostic only: queued GLES reads, release FD transfer, three-slot Vulkan reuse.
// Not linked into the APK renderer or libpyroclient. No optical/FPS claims.
#include "pyroclient.h"
#include "gpu_readback_android.h"
#include <GLES3/gl3.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <poll.h>
#include <unistd.h>
#include <zlib.h>

struct Wave {
    int32_t params[8]{};
    std::vector<unsigned char> bytes;
};
static bool load(const char *path, Wave &wave) {
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    char magic[8]; uint32_t length = 0;
    bool ok = fread(magic, 1, 8, f) == 8 && !memcmp(magic, "PYROWAVE", 8)
        && fread(wave.params, sizeof(wave.params), 1, f) == 1
        && fread(&length, 4, 1, f) == 1 && length && length <= 256u*1024*1024;
    if (ok) { wave.bytes.resize(length); ok = fread(wave.bytes.data(), 1, length, f) == length; }
    fclose(f); return ok;
}
static bool decode(pyroclient *client, const Wave &wave, AHardwareBuffer *&buffer,
                   std::string &error) {
    pyroclient_clear(client);
    if (pyroclient_push_packet(client, wave.bytes.data(), wave.bytes.size()) != 1) {
        error = "Frame did not assemble completely"; return false;
    }
    pyroclient_frame_info info{};
    if (pyroclient_decode(client, &buffer, &info) != 0 || !buffer || !info.complete) {
        error = "Producer decode did not complete"; return false;
    }
    return true;
}
static uint32_t crc(const std::vector<unsigned char> &pixels) {
    return uint32_t(crc32(0, pixels.data(), uInt(pixels.size())));
}
int main(int argc, char **argv) {
    if (argc < 3 || argc > 7) {
        fprintf(stderr, "usage: %s A.wave B.wave [haar|97] [repeats=3] [draws=16] [queue=default|low]\n", argv[0]);
        return 2;
    }
    int wavelet = argc > 3 && !strcmp(argv[3], "97") ? 97 : 2;
    if (argc > 3 && strcmp(argv[3], "haar") && strcmp(argv[3], "97")) return 2;
    int repeats = argc > 4 ? atoi(argv[4]) : 3;
    int draws = argc > 5 ? atoi(argv[5]) : 16;
    if (repeats < 1 || repeats > 8 || draws < 1 || draws > 128) return 2;
    if (argc > 6 && strcmp(argv[6], "default") && strcmp(argv[6], "low")) return 2;
    const bool low_priority = argc > 6 && !strcmp(argv[6], "low");
    Wave a, b;
    if (!load(argv[1], a) || !load(argv[2], b)) return 2;
    for (int i = 0; i < 5; ++i) if (a.params[i] != b.params[i]) return 2;
    int width=a.params[0], height=a.params[1];
    if (width <= 0 || height <= 0 || uint64_t(width)*height*4 > 256ull*1024*1024) return 2;
    pyroclient *client = pyroclient_create_prioritized(width, height, a.params[3] == 1,
                                             a.params[4], 3, wavelet, 2, low_priority);
    if (!client) return 1;
    const std::unique_ptr<pyroclient, decltype(&pyroclient_destroy)> owned(client, &pyroclient_destroy);
    AHardwareBuffer *buffer=nullptr;
    std::string error;
    auto fail = [&]() { fprintf(stderr, "FAIL %s\n", error.c_str()); return 1; };
    std::vector<unsigned char> reference_a, reference_b, actual, latest;
    if (!decode(client,a,buffer,error) || !readback_android_buffer(buffer,false,reference_a,error)
        || !decode(client,b,buffer,error) || !readback_android_buffer(buffer,false,reference_b,error)) return fail();
    if (reference_a == reference_b) { error="Distinct decoded reference images required"; return fail(); }
    int attached=0, reused=0, pending_at_export=0;
    for (int iteration=0; iteration<repeats; ++iteration) {
        if (!decode(client,a,buffer,error)) return fail();
        AHardwareBuffer *source=buffer, *after=nullptr;
        const uint64_t token=pyroclient_output_release_token(source);
        if (!token) { error="Native release support not active; set debug.q3pw.release_fd=1"; return fail(); }
        auto before = [&](EGLDisplay display, std::string &reason) {
            if (!q3pw::has_extension(eglQueryString(display,EGL_EXTENSIONS),"EGL_ANDROID_native_fence_sync")) {
                reason="EGL native fence extension unavailable"; return false;
            }
            auto create=reinterpret_cast<PFNEGLCREATESYNCKHRPROC>(eglGetProcAddress("eglCreateSyncKHR"));
            auto destroy=reinterpret_cast<PFNEGLDESTROYSYNCKHRPROC>(eglGetProcAddress("eglDestroySyncKHR"));
            auto duplicate=reinterpret_cast<PFNEGLDUPNATIVEFENCEFDANDROIDPROC>(eglGetProcAddress("eglDupNativeFenceFDANDROID"));
            if (!create || !destroy || !duplicate) { reason="Native fence entry points missing"; return false; }
            const EGLint attributes[]={EGL_NONE};
            EGLSyncKHR sync=create(display,EGL_SYNC_NATIVE_FENCE_ANDROID,attributes);
            if (sync==EGL_NO_SYNC_KHR) { reason="Native fence creation failed"; return false; }
            glFlush();
            int fd=duplicate(display,sync);
            bool destroyed=destroy(display,sync)==EGL_TRUE;
            if (fd < 0 || !destroyed) { if (fd >= 0) close(fd); reason="Native fence export/destroy failed"; return false; }
            pollfd status{fd,POLLIN,0};
            int observed=poll(&status,1,0);
            if (observed < 0 || (status.revents & (POLLERR|POLLNVAL))) {
                close(fd); reason="Invalid exported fence"; return false;
            }
            if (observed==0) ++pending_at_export;
            // Native API always consumes fd, including a rejected attachment.
            if (!pyroclient_attach_release_fd(source,token,fd)) { reason="Fence attachment rejected"; return false; }
            ++attached;
            // Deliberately cycle all three slots before the outer GLES glFinish/read.
            // The third write must reuse source, protected by the imported release FD.
            for (int i=0;i<3;++i) if (!decode(client,b,after,reason)) return false;
            if (after!=source) { reason="Three-slot reuse was not exercised"; return false; }
            ++reused;
            return true;
        };
        if (!readback_android_buffer(source,false,actual,error,before,draws)) return fail();
        if (actual != reference_a) { error="Queued GLES source reads were corrupted by reuse"; return fail(); }
        if (!readback_android_buffer(after,false,latest,error)) return fail();
        if (latest != reference_b) { error="Reused Vulkan output differs from B reference"; return fail(); }
    }
    printf("{\"probe\":\"release_fd_gpu_reuse\",\"width\":%d,\"height\":%d,\"repeats\":%d,"
           "\"draws_per_read\":%d,\"attached_fds\":%d,\"source_reuses\":%d,\"unsignaled_at_export\":%d,"
           "\"a_crc32\":\"%08x\",\"b_crc32\":\"%08x\",\"queue_requested\":\"%s\",\"exact_a_and_b\":true}\n",
           width,height,repeats,draws,attached,reused,pending_at_export,crc(reference_a),crc(reference_b),
           low_priority ? "low" : "default");
    return 0;
}
