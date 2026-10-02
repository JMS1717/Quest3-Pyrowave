// On-device timing/ownership check of libpyroclient. Use '-' to omit pixel readback.
//   pyroclient_test <in.wave> <-|out.rgba> [iterations]
// Pixel dumps default to an EGL/GLES consumer, without adding CPU allocation flags.
// Explicit CPU dumps require CPU_READ usage; lock success alone is insufficient.
#include "pyroclient.h"
#include "gpu_readback_android.h"
#include <android/hardware_buffer.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <algorithm>
#include <memory>
#include <string>
#include <zlib.h>

// Same container the harness reads: 8-byte magic, 32-byte header, u32 length, frame.
struct Wave { int width = 0, height = 0, chroma = 0, full_range = 0; std::vector<unsigned char> frame; };
static bool load(const char *path, Wave &w) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return false; }
    // "PYROWAVE", then int32 params[8] = {width, height, format, chroma, full_range, fps_num,
    // fps_den, _}, then u32 frame size -- as tools/pyrowave_android/main.cpp WaveFile reads it.
    unsigned char hdr[40];
    if (fread(hdr, 1, 40, f) != 40 || memcmp(hdr, "PYROWAVE", 8) != 0) { fprintf(stderr, "not a .wave\n"); fclose(f); return false; }
    memcpy(&w.width, hdr + 8, 4); memcpy(&w.height, hdr + 12, 4); memcpy(&w.chroma, hdr + 20, 4); memcpy(&w.full_range, hdr + 24, 4);
    unsigned int len = 0;
    if (fread(&len, 1, 4, f) != 4) { fclose(f); return false; }
    if (w.width <= 0 || w.height <= 0 || len == 0 || len > 256u * 1024 * 1024) {
        fprintf(stderr, "invalid dimensions or encoded frame size\n"); fclose(f); return false;
    }
    w.frame.resize(len);
    if (fread(w.frame.data(), 1, len, f) != len) { fprintf(stderr, "short frame\n"); fclose(f); return false; }
    fclose(f);
    return true;
}

int main(int argc, char **argv) {
    if (argc < 3 || argc > 9) { fprintf(stderr, "usage: %s <in.wave> <-|out.rgba> [iterations] [auto|compute|fragment] [warmup_frames] [protect_first_buffer=0|1] [gpu|cpu] [flip_y=0|1]\n", argv[0]); return 1; }
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
    const bool gpu_readback = argc <= 7 || !strcmp(argv[7], "gpu");
    if (!gpu_readback && strcmp(argv[7], "cpu")) return 2;
    if (argc > 8 && strcmp(argv[8], "0") && strcmp(argv[8], "1")) return 2;
    const bool flip_y = argc > 8 && !strcmp(argv[8], "1");
    const std::string metadata_path = std::string(argv[2]) + ".json";
    if (strcmp(argv[2], "-")) {
        // A failed rerun must not leave a previous file looking like a new valid dump.
        FILE *pending = fopen(metadata_path.c_str(), "wb");
        if (!pending) { perror("readback metadata"); return 1; }
        const int written = fprintf(pending, "{\"schema_version\":1,\"readback_complete\":false,\"reason\":\"probe_not_completed\"}\n");
        const int closed = fclose(pending);
        if (written < 0 || closed != 0) return 1;
    }
    printf("input %dx%d %s %s range, %zu bytes\n", w.width, w.height, w.chroma == 1 ? "4:4:4" : "4:2:0", w.full_range ? "full" : "limited", w.frame.size());
    const char *wavelet_name = getenv("PYROWAVE_WAVELET");
    const int wavelet = wavelet_name && !strcmp(wavelet_name, "haar") ? 2 :
                        wavelet_name && !strcmp(wavelet_name, "53") ? 53 : 97;
    pyroclient *c = pyroclient_create_ex((uint32_t)w.width, (uint32_t)w.height, w.chroma == 1, w.full_range, 3, wavelet, hint);
    if (!c) { fprintf(stderr, "pyroclient_create failed (see logcat pyroclient)\n"); return 1; }
    const std::unique_ptr<pyroclient, decltype(&pyroclient_destroy)> owned(c, &pyroclient_destroy);
    AHardwareBuffer *ahb = nullptr;
    pyroclient_frame_info info{};
    double bestDec = 1e9, bestConv = 1e9, bestTot = 1e9, sumTot = 0;
    std::vector<double> totals, decodes, converts, records, waits;
    double warmupMax = 0;
    int completes = 0;
    AHardwareBuffer *held = nullptr;
    const bool protect_first = argc > 6 && atoi(argv[6]) != 0;
    for (int i = 0; i < iters + warmup; i++) {
        // Re-pushing the same frame reads as an old sequence number and is dropped; a decode
        // would then run on nothing. Clear first, as the harness does.
        if (i > 0) pyroclient_clear(c);
        int r = pyroclient_push_packet(c, w.frame.data(), w.frame.size());
        if (r < 0) { fprintf(stderr, "push failed %d\n", r); return 1; }
        AHardwareBuffer *previous = ahb;
        int decoded = protect_first ? pyroclient_decode_guarded(c, &ahb, &info, held, previous)
                                    : pyroclient_decode(c, &ahb, &info);
        if (decoded != 0) { fprintf(stderr, "decode failed\n"); return 1; }
        if (protect_first && (ahb == held || ahb == previous)) {
            fprintf(stderr, "protected hardware buffer was recycled\n"); return 1;
        }
        if (protect_first && !held) held = ahb;
        if (i < warmup) { warmupMax = std::max(warmupMax, info.total_ms); continue; }
        decodes.push_back(info.decode_ms); converts.push_back(info.convert_ms);
        records.push_back(info.record_ms); waits.push_back(info.wait_ms);
        if (info.decode_ms < bestDec) bestDec = info.decode_ms;
        if (info.convert_ms < bestConv) bestConv = info.convert_ms;
        if (info.total_ms < bestTot) bestTot = info.total_ms;
        sumTot += info.total_ms;
        totals.push_back(info.total_ms);
        completes += info.complete;
    }
    std::sort(totals.begin(), totals.end());
    std::sort(decodes.begin(), decodes.end()); std::sort(converts.begin(), converts.end());
    std::sort(records.begin(), records.end()); std::sort(waits.begin(), waits.end());
    printf("complete %d/%d  decode best %.3f ms  convert best %.3f ms  submit->fence best %.3f p50 %.3f max %.3f ms\n",
           completes, iters, bestDec, bestConv, bestTot, totals[totals.size() / 2], totals.back());
    printf("{\"requested_decode_path\":\"%s\",\"iterations\":%d,\"complete\":%d,\"decode_best_ms\":%.6f,\"convert_best_ms\":%.6f,\"fence_p50_ms\":%.6f,\"fence_p99_ms\":%.6f,\"fence_mean_ms\":%.6f,\"fence_max_ms\":%.6f,\"warmup_frames\":%d,\"warmup_max_fence_ms\":%.6f,\"gpu_decode_p50_ms\":%.6f,\"gpu_decode_p99_ms\":%.6f,\"convert_p50_ms\":%.6f,\"record_p50_ms\":%.6f,\"wait_p50_ms\":%.6f}\n",
        argc > 4 ? argv[4] : "auto", iters, completes, bestDec, bestConv, totals[totals.size()/2], totals[(totals.size()-1)*99/100],sumTot/iters,totals.back(),warmup,warmupMax,decodes[decodes.size()/2],decodes[(decodes.size()-1)*99/100],converts[converts.size()/2],records[records.size()/2],waits[waits.size()/2]);

    if (!strcmp(argv[2], "-")) {
        printf("pixel readback skipped; timings do not establish image correctness\n");
        return completes == iters ? 0 : 1;
    }
    if (completes != iters || !ahb || !info.complete) {
        fprintf(stderr, "pixel readback refused: producer did not complete every measured frame\n");
        return 1;
    }
    AHardwareBuffer_Desc d = {};
    AHardwareBuffer_describe(ahb, &d);
    if (!d.width || !d.height || d.layers != 1 ||
        d.format != AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM ||
        uint64_t(d.width) * d.height > (256ull * 1024 * 1024) / 4) return 2;
    std::vector<unsigned char> pixels;
    const uint64_t cpu_read = d.usage & AHARDWAREBUFFER_USAGE_CPU_READ_MASK;
    if (gpu_readback) {
        std::string error;
        if (!readback_android_buffer(ahb, flip_y, pixels, error)) {
            fprintf(stderr, "GPU readback failed: %s\n", error.c_str());
            return 2;
        }
    } else {
      if (cpu_read != AHARDWAREBUFFER_USAGE_CPU_READ_RARELY &&
        cpu_read != AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN) {
        fprintf(stderr, "CPU dump refused: allocation has no CPU_READ usage. Use '-' for timing only; validate pixels through a GPU consumer.\n");
        return 2;
      }
      pixels.resize(size_t(d.width) * d.height * 4);
      void *mapped = nullptr;
      if (AHardwareBuffer_lock(ahb, cpu_read, -1, nullptr, &mapped) != 0 || !mapped) {
        fprintf(stderr, "AHardwareBuffer_lock failed\n");
        return 2;
      }
      for (unsigned y = 0; y < d.height; y++) {
        const unsigned source_y = flip_y ? d.height - 1 - y : y;
        memcpy(pixels.data() + size_t(y) * d.width * 4,
               static_cast<const unsigned char *>(mapped) + size_t(source_y) * d.stride * 4,
               size_t(d.width) * 4);
      }
      if (AHardwareBuffer_unlock(ahb, nullptr) != 0) {
        fprintf(stderr, "AHardwareBuffer_unlock failed\n"); return 2;
      }
    }
    FILE *out = fopen(argv[2], "wb");
    bool wrote = out != nullptr;
    if (out) {
        if (fwrite(pixels.data(), 1, pixels.size(), out) != pixels.size()) wrote = false;
        if (fclose(out) != 0) wrote = false;
    }
    if (!wrote) { fprintf(stderr, "pixel dump could not be written completely\n"); return 1; }
    FILE *metadata = fopen(metadata_path.c_str(), "wb");
    if (!metadata) { perror("readback metadata"); return 1; }
    const char *row_order = gpu_readback ? (flip_y ? "texture_v1_first" : "texture_v0_first")
                                        : (flip_y ? "buffer_last_row_first" : "buffer_row0_first");
    const uint32_t checksum = uint32_t(crc32(0, pixels.data(), static_cast<uInt>(pixels.size())));
    const int written = fprintf(metadata,
        "{\"schema_version\":1,\"readback_complete\":true,\"readback_method\":\"%s\",\"width\":%u,\"height\":%u,\"bytes\":%zu,\"source_ahb_usage\":%llu,\"row_order\":\"%s\",\"flip_y\":%s,\"raw_rgba_crc32\":\"%08x\",\"outside_decode_timing\":true,\"producer_complete\":true}\n",
        gpu_readback ? "gles_external_rgba8" : "cpu_usage_compatible", d.width, d.height,
        pixels.size(), static_cast<unsigned long long>(d.usage), row_order,
        flip_y ? "true" : "false", checksum);
    const int closed = fclose(metadata);
    if (written < 0 || closed != 0) { fprintf(stderr, "readback metadata write failed\n"); return 1; }
    printf("wrote %s (%ux%u rgba; %s; %s; readback excluded from decode timing)\n",
           argv[2], d.width, d.height, gpu_readback ? "GPU readback" : "CPU readback", row_order);
    return 0;
}
