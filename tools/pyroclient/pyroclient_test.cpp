// On-device check of libpyroclient: decode a .wave through the library N times and dump the RGBA
// buffer, so the library is scored against the reference before ALVR ever loads it.
//   pyroclient_test <in.wave> <out.rgba> [iterations]
// out.rgba is width*height*4 bytes; convert with ffmpeg -f rawvideo -pix_fmt rgba -s WxH.
#include "pyroclient.h"
#include <android/hardware_buffer.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <algorithm>

// Same container the harness reads: 8-byte magic, 32-byte header, u32 length, frame.
struct Wave { int width = 0, height = 0, chroma = 0, full_range = 0; std::vector<unsigned char> frame; };
static bool load(const char *path, Wave &w) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return false; }
    // "PYROWAVE", then int32 params[8] = {width, height, format, chroma, full_range, fps_num,
    // fps_den, _}, then u32 frame size -- as tools/pyrowave_android/main.cpp WaveFile reads it.
    unsigned char hdr[40];
    if (fread(hdr, 1, 40, f) != 40 || memcmp(hdr, "PYROWAVE", 8) != 0) { fprintf(stderr, "not a .wave\n"); return false; }
    memcpy(&w.width, hdr + 8, 4); memcpy(&w.height, hdr + 12, 4); memcpy(&w.chroma, hdr + 20, 4); memcpy(&w.full_range, hdr + 24, 4);
    unsigned int len = 0;
    if (fread(&len, 1, 4, f) != 4) return false;
    w.frame.resize(len);
    if (fread(w.frame.data(), 1, len, f) != len) { fprintf(stderr, "short frame\n"); return false; }
    fclose(f);
    return true;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s <in.wave> <out.rgba> [iterations] [auto|compute|fragment] [warmup_frames]\n", argv[0]); return 1; }
    Wave w;
    if (!load(argv[1], w)) return 1;
    const int iters = argc > 3 ? atoi(argv[3]) : 1;
    if (iters < 1 || iters > 100000) return 2;
    const int warmup = argc > 5 ? atoi(argv[5]) : (iters > 1 ? 20 : 0);
    if (warmup < 0 || warmup > 10000) return 2;
    int hint = 0;
    if (argc > 4) {
        if (!strcmp(argv[4], "compute")) hint = 2;
        else if (!strcmp(argv[4], "fragment")) hint = 1;
        else if (strcmp(argv[4], "auto")) return 2;
    }
    printf("input %dx%d %s %s range, %zu bytes\n", w.width, w.height, w.chroma == 1 ? "4:4:4" : "4:2:0", w.full_range ? "full" : "limited", w.frame.size());
    pyroclient *c = pyroclient_create_ex((uint32_t)w.width, (uint32_t)w.height, w.chroma == 1, w.full_range, 3, getenv("PYROWAVE_WAVELET") && !strcmp(getenv("PYROWAVE_WAVELET"), "53") ? 53 : 97, hint);
    if (!c) { fprintf(stderr, "pyroclient_create failed (see logcat pyroclient)\n"); return 1; }
    AHardwareBuffer *ahb = nullptr;
    pyroclient_frame_info info{};
    double bestDec = 1e9, bestConv = 1e9, bestTot = 1e9, sumTot = 0;
    std::vector<double> totals, decodes, converts;
    double warmupMax = 0;
    int completes = 0;
    for (int i = 0; i < iters + warmup; i++) {
        // Re-pushing the same frame reads as an old sequence number and is dropped; a decode
        // would then run on nothing. Clear first, as the harness does.
        if (i > 0) pyroclient_clear(c);
        int r = pyroclient_push_packet(c, w.frame.data(), w.frame.size());
        if (r < 0) { fprintf(stderr, "push failed %d\n", r); return 1; }
        if (pyroclient_decode(c, &ahb, &info) != 0) { fprintf(stderr, "decode failed\n"); return 1; }
        if (i < warmup) { warmupMax = std::max(warmupMax, info.total_ms); continue; }
        decodes.push_back(info.decode_ms); converts.push_back(info.convert_ms);
        if (info.decode_ms < bestDec) bestDec = info.decode_ms;
        if (info.convert_ms < bestConv) bestConv = info.convert_ms;
        if (info.total_ms < bestTot) bestTot = info.total_ms;
        sumTot += info.total_ms;
        totals.push_back(info.total_ms);
        completes += info.complete;
    }
    std::sort(totals.begin(), totals.end());
    std::sort(decodes.begin(), decodes.end()); std::sort(converts.begin(), converts.end());
    printf("complete %d/%d  decode best %.3f ms  convert best %.3f ms  submit->fence best %.3f p50 %.3f max %.3f ms\n",
           completes, iters, bestDec, bestConv, bestTot, totals[totals.size() / 2], totals.back());
    printf("{\"requested_decode_path\":\"%s\",\"iterations\":%d,\"complete\":%d,\"decode_best_ms\":%.6f,\"convert_best_ms\":%.6f,\"fence_p50_ms\":%.6f,\"fence_p99_ms\":%.6f,\"fence_mean_ms\":%.6f,\"fence_max_ms\":%.6f,\"warmup_frames\":%d,\"warmup_max_fence_ms\":%.6f,\"gpu_decode_p50_ms\":%.6f,\"gpu_decode_p99_ms\":%.6f,\"convert_p50_ms\":%.6f}\n",
        argc > 4 ? argv[4] : "auto", iters, completes, bestDec, bestConv, totals[totals.size()/2], totals[(totals.size()-1)*99/100],sumTot/iters,totals.back(),warmup,warmupMax,decodes[decodes.size()/2],decodes[(decodes.size()-1)*99/100],converts[converts.size()/2]);

    AHardwareBuffer_Desc d = {};
    AHardwareBuffer_describe(ahb, &d);
    void *mapped = nullptr;
    if (AHardwareBuffer_lock(ahb, AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN, -1, nullptr, &mapped) != 0 || !mapped) {
        fprintf(stderr, "AHardwareBuffer_lock failed (the buffer has no CPU usage; that is fine for GLES, not for this dump)\n");
        return 2;
    }
    FILE *out = fopen(argv[2], "wb");
    for (unsigned y = 0; y < d.height; y++)
        fwrite((const unsigned char *)mapped + (size_t)y * d.stride * 4, 1, (size_t)d.width * 4, out);
    fclose(out);
    AHardwareBuffer_unlock(ahb, nullptr);
    printf("wrote %s (%ux%u rgba, stride %u)\n", argv[2], d.width, d.height, d.stride);
    pyroclient_destroy(c);
    return 0;
}
