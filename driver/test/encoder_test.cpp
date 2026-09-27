// Standalone exercise for xrwired::Encoder: encode a moving pattern from a D3D11 RGBA texture and report
// bitrate and per-frame encode time, then re-run through the "texture NVENC cannot take directly" path.
//
//   encoder_test [--frames 120] [--w 3264] [--h 1408] [--fps 72] [--mbps 400] [--preset 1] [--tuning ll]
//                [--noise 24] [--out out.h264] [--fallback-out out_fallback.h264] [--hevc] [--10bit]
//
// Writes the raw Annex B stream so ffmpeg/ffprobe can check it decodes. Exit code 0 only if every frame
// encodes, the first access unit carries SPS+IDR, later access units carry no IDR, and the measured
// bitrate lands within 15% of the request.
#define NOMINMAX
#define _CRT_SECURE_NO_WARNINGS
#include <d3d11.h>
#include <windows.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "encoder.h"

#pragma comment(lib, "d3d11.lib")

namespace {

struct Args {
    int frames = 120, w = 3264, h = 1408, fps = 72, mbps = 400, preset = 1, noise = 24;
    std::string tuning = "ll", out = "out.h264", fallback_out = "out_fallback.h264";
    bool hevc = false, ten_bit = false;
};

Args parse(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; ++i) {
        std::string k = argv[i];
        if (k == "--hevc") { a.hevc = true; continue; }
        if (k == "--10bit") { a.ten_bit = true; continue; }
        if (i + 1 >= argc) break;
        std::string v = argv[++i];
        if (k == "--frames") a.frames = std::stoi(v);
        else if (k == "--w") a.w = std::stoi(v);
        else if (k == "--h") a.h = std::stoi(v);
        else if (k == "--fps") a.fps = std::stoi(v);
        else if (k == "--mbps") a.mbps = std::stoi(v);
        else if (k == "--preset") a.preset = std::stoi(v);
        else if (k == "--noise") a.noise = std::stoi(v);
        else if (k == "--tuning") a.tuning = v;
        else if (k == "--out") a.out = v;
        else if (k == "--fallback-out") a.fallback_out = v;
    }
    return a;
}

// Scrolling coarse blocks plus a moving diagonal edge: enough motion that rate control has real work.
// `noise` adds independent per-pixel temporal noise; without it the pattern compresses so well that CBR
// (which never stuffs bits) settles well under the target, like a static scene would.
void fill_pattern(uint8_t* base, uint32_t pitch, int w, int h, int frame, int noise) {
    const int scroll = frame * 7;
    uint32_t rng = uint32_t(frame) * 2654435761u + 1u;
    for (int y = 0; y < h; ++y) {
        uint8_t* row = base + size_t(y) * pitch;
        const int by = (y >> 5);
        for (int x = 0; x < w; ++x) {
            const int bx = ((x + scroll) >> 5);
            const uint8_t block = uint8_t(((bx * 37 + by * 61) & 0x7) * 28 + 20);
            const bool edge = ((x + y * 2 + frame * 23) % 512) < 40;
            int r = edge ? 235 : block;
            int g = edge ? 16 : (block + by * 3) & 0xff;
            int b = edge ? 128 : (block + bx * 5) & 0xff;
            if (noise > 0) {
                rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;   // xorshift32
                const int n = int((rng >> 8) % uint32_t(2 * noise + 1)) - noise;
                r = std::clamp(r + n, 0, 255);
                g = std::clamp(g + n, 0, 255);
                b = std::clamp(b + n, 0, 255);
            }
            row[x * 4 + 0] = uint8_t(r);
            row[x * 4 + 1] = uint8_t(g);
            row[x * 4 + 2] = uint8_t(b);
            row[x * 4 + 3] = 255;
        }
    }
}

// Annex B walk: true if the access unit contains a NAL of one of the wanted types.
bool has_nal(const std::vector<uint8_t>& au, size_t begin, size_t end, bool hevc, const std::vector<int>& types) {
    for (size_t i = begin; i + 4 < end; ++i) {
        if (!(au[i] == 0 && au[i + 1] == 0 && au[i + 2] == 1)) continue;
        const uint8_t b = au[i + 3];
        const int type = hevc ? ((b >> 1) & 0x3f) : (b & 0x1f);
        if (std::find(types.begin(), types.end(), type) != types.end()) return true;
    }
    return false;
}

ID3D11Texture2D* make_texture(ID3D11Device* device, int w, int h, UINT bind, D3D11_USAGE usage, UINT cpu) {
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = UINT(w);
    td.Height = UINT(h);
    td.MipLevels = 1;
    td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = usage;
    td.BindFlags = bind;
    td.CPUAccessFlags = cpu;
    ID3D11Texture2D* tex = nullptr;
    if (FAILED(device->CreateTexture2D(&td, nullptr, &tex))) return nullptr;
    return tex;
}

}  // namespace

int main(int argc, char** argv) {
    const Args a = parse(argc, argv);

    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION,
                                 &device, nullptr, &context))) {
        std::fprintf(stderr, "FAIL: D3D11CreateDevice\n");
        return 1;
    }

    xrwired::EncoderConfig cfg;
    cfg.width = a.w;
    cfg.height = a.h;
    cfg.fps = a.fps;
    cfg.mbps = a.mbps;
    cfg.hevc = a.hevc;
    cfg.ten_bit = a.ten_bit;
    cfg.preset = a.preset;
    cfg.tuning = a.tuning;

    xrwired::Encoder encoder;
    if (!encoder.init(device, cfg)) {
        std::fprintf(stderr, "FAIL: init: %s\n", encoder.error().c_str());
        return 2;
    }

    ID3D11Texture2D* staging = make_texture(device, a.w, a.h, 0, D3D11_USAGE_STAGING, D3D11_CPU_ACCESS_WRITE);
    ID3D11Texture2D* frame = make_texture(device, a.w, a.h, D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE,
                                          D3D11_USAGE_DEFAULT, 0);
    if (!staging || !frame) {
        std::fprintf(stderr, "FAIL: CreateTexture2D\n");
        return 1;
    }
    ID3D11Query* done = nullptr;
    D3D11_QUERY_DESC qd = {D3D11_QUERY_EVENT, 0};
    device->CreateQuery(&qd, &done);

    FILE* out = std::fopen(a.out.c_str(), "wb");
    if (!out) {
        std::fprintf(stderr, "FAIL: cannot open %s\n", a.out.c_str());
        return 1;
    }

    std::vector<uint8_t> au;
    std::vector<double> ms;
    size_t total_bytes = 0;
    int failures = 0, missing_headers = 0, stray_idr = 0;
    for (int f = 0; f < a.frames; ++f) {
        D3D11_MAPPED_SUBRESOURCE m;
        if (FAILED(context->Map(staging, 0, D3D11_MAP_WRITE, 0, &m))) {
            std::fprintf(stderr, "FAIL: Map staging\n");
            return 1;
        }
        fill_pattern(static_cast<uint8_t*>(m.pData), m.RowPitch, a.w, a.h, f, a.noise);
        context->Unmap(staging, 0);
        context->CopyResource(frame, staging);
        context->End(done);                                    // upload lands before the encode clock starts
        while (context->GetData(done, nullptr, 0, 0) == S_FALSE) {}

        au.clear();
        const bool idr = f == 0;
        if (!encoder.encode(frame, uint64_t(f) * 1000000ull / uint64_t(a.fps), idr, &au)) {
            std::fprintf(stderr, "FAIL: encode frame %d: %s\n", f, encoder.error().c_str());
            ++failures;
            break;
        }
        ms.push_back(encoder.last_encode_ms());
        total_bytes += au.size();

        // First access unit must be self-contained (parameter sets + IDR); the rest must not restart the GOP.
        const std::vector<int> headers = a.hevc ? std::vector<int>{33, 34} : std::vector<int>{7, 8};
        const std::vector<int> idr_nal = a.hevc ? std::vector<int>{19, 20} : std::vector<int>{5};
        if (idr) {
            if (!has_nal(au, 0, au.size(), a.hevc, headers) || !has_nal(au, 0, au.size(), a.hevc, idr_nal))
                ++missing_headers;
        } else if (has_nal(au, 0, au.size(), a.hevc, idr_nal)) {
            ++stray_idr;
        }
        std::fwrite(au.data(), 1, au.size(), out);
    }
    std::fclose(out);

    // A texture NVENC cannot register directly: no BIND_RENDER_TARGET and the wrong size. The encoder is
    // expected to copy it into an internal texture rather than refuse it.
    int fallback_frames = 0, fallback_failures = 0;
    {
        xrwired::Encoder fb;
        if (!fb.init(device, cfg)) {
            std::fprintf(stderr, "FAIL: fallback init: %s\n", fb.error().c_str());
            return 2;
        }
        ID3D11Texture2D* odd_staging =
            make_texture(device, a.w / 2, a.h / 2, 0, D3D11_USAGE_STAGING, D3D11_CPU_ACCESS_WRITE);
        ID3D11Texture2D* odd = make_texture(device, a.w / 2, a.h / 2, D3D11_BIND_SHADER_RESOURCE,
                                            D3D11_USAGE_DEFAULT, 0);
        FILE* fout = std::fopen(a.fallback_out.c_str(), "wb");
        if (!odd || !odd_staging || !fout) {
            std::fprintf(stderr, "FAIL: fallback setup\n");
            return 1;
        }
        for (int f = 0; f < 10; ++f) {
            D3D11_MAPPED_SUBRESOURCE m;
            if (FAILED(context->Map(odd_staging, 0, D3D11_MAP_WRITE, 0, &m))) return 1;
            fill_pattern(static_cast<uint8_t*>(m.pData), m.RowPitch, a.w / 2, a.h / 2, f, a.noise);
            context->Unmap(odd_staging, 0);
            context->CopyResource(odd, odd_staging);
            au.clear();
            if (!fb.encode(odd, uint64_t(f) * 1000000ull / uint64_t(a.fps), f == 0, &au)) {
                std::fprintf(stderr, "FAIL: fallback encode frame %d: %s\n", f, fb.error().c_str());
                ++fallback_failures;
                break;
            }
            std::fwrite(au.data(), 1, au.size(), fout);
            ++fallback_frames;
        }
        std::fclose(fout);
        odd->Release();
        odd_staging->Release();
    }

    if (ms.empty()) {
        std::fprintf(stderr, "FAIL: no frames encoded\n");
        return 3;
    }
    const double mbps = total_bytes * 8.0 * a.fps / double(ms.size()) / 1e6;
    std::vector<double> sorted = ms;
    std::sort(sorted.begin(), sorted.end());
    auto pct = [&](double q) { return sorted[std::min(sorted.size() - 1, size_t(sorted.size() * q))]; };

    std::printf("codec            %s\n", a.hevc ? "HEVC" : "H.264 CAVLC");
    std::printf("frames           %d (%d failed)\n", int(ms.size()), failures);
    std::printf("resolution       %dx%d @%d\n", a.w, a.h, a.fps);
    std::printf("bitrate          %.1f Mbps (target %d, %+.1f%%)\n", mbps, a.mbps,
                100.0 * (mbps - a.mbps) / a.mbps);
    std::printf("bytes            %llu -> %s\n", (unsigned long long)total_bytes, a.out.c_str());
    std::printf("encode ms        p50 %.2f  p95 %.2f  p99 %.2f  max %.2f  first %.2f\n", pct(0.5), pct(0.95),
                pct(0.99), sorted.back(), ms.front());
    std::printf("first AU         %s\n", missing_headers ? "MISSING SPS/PPS or IDR" : "SPS/PPS + IDR present");
    std::printf("later AUs        %s\n", stray_idr ? "UNEXPECTED IDR" : "no unrequested IDR");
    std::printf("odd texture      %d frames re-encoded via internal copy (%d failed) -> %s\n", fallback_frames,
                fallback_failures, a.fallback_out.c_str());

    bool ok = failures == 0 && missing_headers == 0 && stray_idr == 0 && int(ms.size()) == a.frames &&
              fallback_failures == 0 && fallback_frames == 10;
    if (std::abs(mbps - a.mbps) > 0.15 * a.mbps) {
        std::printf("bitrate outside +/-15%% of target\n");
        ok = false;
    }
    std::printf("%s\n", ok ? "PASS" : "FAIL");

    encoder.shutdown();
    if (done) done->Release();
    frame->Release();
    staging->Release();
    context->Release();
    device->Release();
    return ok ? 0 : 4;
}
