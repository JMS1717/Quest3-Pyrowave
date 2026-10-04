// SPDX-License-Identifier: MIT
// On-device check of PYROCLIENT_FLAG_PLANAR_OUTPUT: decodes one frame through the RGBA path and
// the planar path, reads every buffer back through GLES external sampling, converts the planes on
// the CPU exactly as convert.frag does, and compares. Also times both paths on the same frame.
//   planar_output_test <in.wave> [iterations=200] [warmup=20] [auto|fragment|compute]
// PYROWAVE_WAVELET=haar|53|97 (default haar). Exit 0 pass, 1 failure, 3 planar unsupported.
#include "pyroclient.h"
#include "gpu_readback_android.h"
#include <android/hardware_buffer.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

struct Wave { int width = 0, height = 0, chroma = 0, full_range = 0; std::vector<unsigned char> frame; };
static bool load(const char *path, Wave &w) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return false; }
    unsigned char hdr[40];
    unsigned int len = 0;
    bool ok = fread(hdr, 1, 40, f) == 40 && !memcmp(hdr, "PYROWAVE", 8) && fread(&len, 1, 4, f) == 4;
    if (ok) {
        memcpy(&w.width, hdr + 8, 4); memcpy(&w.height, hdr + 12, 4);
        memcpy(&w.chroma, hdr + 20, 4); memcpy(&w.full_range, hdr + 24, 4);
        ok = w.width > 0 && w.height > 0 && len && len <= 256u * 1024 * 1024;
    }
    if (ok) { w.frame.resize(len); ok = fread(w.frame.data(), 1, len, f) == len; }
    fclose(f);
    return ok;
}

struct Timing { double decode = 0, convert = 0, fence = 0, wait = 0; };
static double p50(std::vector<double> v) { std::sort(v.begin(), v.end()); return v[v.size() / 2]; }

static bool run(pyroclient *c, const Wave &w, int iters, int warmup, AHardwareBuffer *&last, Timing &t) {
    std::vector<double> decode, convert, fence, wait;
    pyroclient_frame_info info{};
    for (int i = 0; i < iters + warmup; i++) {
        if (i > 0) pyroclient_clear(c);
        if (pyroclient_push_packet(c, w.frame.data(), w.frame.size()) != 1) { fprintf(stderr, "push incomplete\n"); return false; }
        if (pyroclient_decode(c, &last, &info) != 0 || !info.complete) { fprintf(stderr, "decode failed\n"); return false; }
        if (i < warmup) continue;
        decode.push_back(info.decode_ms); convert.push_back(info.convert_ms);
        fence.push_back(info.total_ms); wait.push_back(info.wait_ms);
    }
    t = { p50(decode), p50(convert), p50(fence), p50(wait) };
    return true;
}

static bool read_red(AHardwareBuffer *buffer, std::vector<unsigned char> &plane, uint32_t &w, uint32_t &h) {
    AHardwareBuffer_Desc d{};
    AHardwareBuffer_describe(buffer, &d);
    w = d.width; h = d.height;
    std::vector<unsigned char> rgba;
    std::string error;
    if (!readback_android_buffer(buffer, false, rgba, error)) { fprintf(stderr, "plane readback: %s\n", error.c_str()); return false; }
    plane.resize(size_t(w) * h);
    for (size_t i = 0; i < plane.size(); i++) plane[i] = rgba[i * 4];
    return true;
}

static float bilinear(const std::vector<unsigned char> &p, uint32_t w, uint32_t h, float x, float y) {
    x = std::min(std::max(x, 0.0f), float(w - 1)); y = std::min(std::max(y, 0.0f), float(h - 1));
    const uint32_t x0 = uint32_t(x), y0 = uint32_t(y);
    const uint32_t x1 = std::min(x0 + 1, w - 1), y1 = std::min(y0 + 1, h - 1);
    const float fx = x - x0, fy = y - y0;
    auto at = [&](uint32_t xx, uint32_t yy) { return p[size_t(yy) * w + xx] / 255.0f; };
    return (at(x0, y0) * (1 - fx) + at(x1, y0) * fx) * (1 - fy) + (at(x0, y1) * (1 - fx) + at(x1, y1) * fx) * fy;
}

int main(int argc, char **argv) {
    if (argc < 2 || argc > 5) { fprintf(stderr, "usage: %s <in.wave> [iterations] [warmup] [auto|fragment|compute]\n", argv[0]); return 1; }
    Wave w;
    if (!load(argv[1], w)) return 1;
    const int iters = argc > 2 ? atoi(argv[2]) : 200, warmup = argc > 3 ? atoi(argv[3]) : 20;
    if (iters < 1 || iters > 100000 || warmup < 0 || warmup > 10000) return 1;
    int hint = 0;
    if (argc > 4) {
        if (!strcmp(argv[4], "compute")) hint = 2;
        else if (!strcmp(argv[4], "fragment")) hint = 1;
        else if (strcmp(argv[4], "auto")) return 1;
    }
    const char *wavelet_name = getenv("PYROWAVE_WAVELET");
    const int wavelet = !wavelet_name || !strcmp(wavelet_name, "haar") ? 2 : !strcmp(wavelet_name, "53") ? 53 : 97;
    printf("input %dx%d %s %s range, %zu bytes, wavelet %d\n", w.width, w.height, w.chroma == 1 ? "4:4:4" : "4:2:0",
           w.full_range ? "full" : "limited", w.frame.size(), wavelet);

    std::vector<unsigned char> rgba;
    Timing rgba_t;
    {
        pyroclient *raw = pyroclient_create_flags(w.width, w.height, w.chroma == 1, w.full_range, 3, wavelet, hint, 0);
        if (!raw) { fprintf(stderr, "RGBA create failed\n"); return 1; }
        std::unique_ptr<pyroclient, decltype(&pyroclient_destroy)> c(raw, &pyroclient_destroy);
        AHardwareBuffer *out = nullptr;
        if (!run(raw, w, iters, warmup, out, rgba_t)) return 1;
        std::string error;
        if (!readback_android_buffer(out, false, rgba, error)) { fprintf(stderr, "RGBA readback: %s\n", error.c_str()); return 1; }
    }

    pyroclient *raw = pyroclient_create_flags(w.width, w.height, w.chroma == 1, w.full_range, 3, wavelet, hint,
                                              PYROCLIENT_FLAG_PLANAR_OUTPUT);
    if (!raw) { fprintf(stderr, "planar create failed\n"); return 1; }
    std::unique_ptr<pyroclient, decltype(&pyroclient_destroy)> c(raw, &pyroclient_destroy);
    if (!pyroclient_is_planar(raw)) { printf("PLANAR_UNSUPPORTED (fell back to RGBA; see logcat Q3PW_PLANAR)\n"); return 3; }
    AHardwareBuffer *luma = nullptr, *cb = nullptr, *cr = nullptr;
    Timing planar_t;
    if (!run(raw, w, iters, warmup, luma, planar_t)) return 1;
    if (!pyroclient_output_planes(luma, &cb, &cr)) { fprintf(stderr, "no planes registered for Y buffer\n"); return 1; }
    std::vector<unsigned char> y, u, v;
    uint32_t yw, yh, cw, ch, cw2, ch2;
    if (!read_red(luma, y, yw, yh) || !read_red(cb, u, cw, ch) || !read_red(cr, v, cw2, ch2)) return 1;
    const uint32_t ew = w.chroma == 1 ? w.width : w.width / 2, eh = w.chroma == 1 ? w.height : w.height / 2;
    if (yw != uint32_t(w.width) || yh != uint32_t(w.height) || cw != ew || ch != eh || cw2 != ew || ch2 != eh) {
        fprintf(stderr, "plane sizes Y %ux%u Cb %ux%u Cr %ux%u\n", yw, yh, cw, ch, cw2, ch2); return 1;
    }

    int max_diff = 0; uint64_t sum_diff = 0, over2 = 0;
    const bool limited = !w.full_range;
    for (uint32_t py = 0; py < yh; py++) {
        for (uint32_t px = 0; px < yw; px++) {
            const float sx = (px + 0.5f) * cw / yw - 0.5f, sy = (py + 0.5f) * ch / yh - 0.5f;
            float Y = y[size_t(py) * yw + px] / 255.0f, Cb = bilinear(u, cw, ch, sx, sy), Cr = bilinear(v, cw, ch, sx, sy);
            if (limited) {
                Y = (Y - 16.0f / 255.0f) * (255.0f / 219.0f);
                Cb = (Cb - 128.0f / 255.0f) * (255.0f / 224.0f);
                Cr = (Cr - 128.0f / 255.0f) * (255.0f / 224.0f);
            } else { Cb -= 0.5f; Cr -= 0.5f; }
            const float rgb[3] = { Y + 1.5748f * Cr, Y - 0.1873f * Cb - 0.4681f * Cr, Y + 1.8556f * Cb };
            for (int k = 0; k < 3; k++) {
                const int expected = int(std::lround(std::min(std::max(rgb[k], 0.0f), 1.0f) * 255.0f));
                const int diff = std::abs(expected - int(rgba[(size_t(py) * yw + px) * 4 + k]));
                max_diff = std::max(max_diff, diff); sum_diff += diff; over2 += diff > 2;
            }
        }
    }
    const double mean_diff = double(sum_diff) / (double(yw) * yh * 3);
    const bool pass = max_diff <= 3 && over2 * 10000 <= uint64_t(yw) * yh * 3;
    printf("{\"iterations\":%d,\"rgba\":{\"gpu_decode_p50_ms\":%.4f,\"convert_p50_ms\":%.4f,\"fence_p50_ms\":%.4f,\"wait_p50_ms\":%.4f},"
           "\"planar\":{\"gpu_decode_p50_ms\":%.4f,\"convert_p50_ms\":%.4f,\"fence_p50_ms\":%.4f,\"wait_p50_ms\":%.4f},"
           "\"compare\":{\"max_abs\":%d,\"mean_abs\":%.5f,\"over2\":%llu,\"pass\":%s}}\n",
           iters, rgba_t.decode, rgba_t.convert, rgba_t.fence, rgba_t.wait,
           planar_t.decode, planar_t.convert, planar_t.fence, planar_t.wait,
           max_diff, mean_diff, (unsigned long long)over2, pass ? "true" : "false");
    printf("%s\n", pass ? "PLANAR_PASS" : "PLANAR_MISMATCH");
    return pass ? 0 : 1;
}
