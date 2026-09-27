// mvhevc_clip: encode a synthetic clip with NVENC and time each frame. MV-HEVC stereo by default, for the
// headset decoder tests; H.264 / HEVC single-view to measure the encoder's share of end-to-end latency.
//
//   mvhevc_clip --w 2560 --h 1440 --fps 72 --mbps 200 --frames 432 [--codec mvhevc|h264|hevc] [--10bit]
//               [--preset 1..7] [--tuning hq|ll|ull] [--noise 0..64] [--input sys|d3d11] [--out clip.mvhevc]
//
// Content: coarse 8 px block texture scrolling horizontally, right eye = left eye shifted by a fixed
// disparity (so inter-view prediction has real work), plus independent per-eye temporal noise of amplitude
// --noise (0 = clean, 64 = stress). --mbps is per view (NVENC's MV-HEVC rate control is per view).
// Output (optional): one record per access unit, [u32 big-endian length][Annex B bytes of every view].
// --input sys: NVENC system-memory NV12 buffers. --input d3d11: NV12 D3D11 textures registered with NVENC,
// like a streamer that encodes the compositor's texture; the upload finishes before the clock starts.
// Timing: nvEncEncodePicture of the first view -> nvEncLockBitstream of the last view returns (synchronous).
// MV-HEVC only initializes with --tuning hq (the driver rejects ll/ull with NV_ENC_ERR_INVALID_PARAM).
#define NOMINMAX
#define _CRT_SECURE_NO_WARNINGS
#include <d3d11.h>
#include <windows.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <string>
#include <vector>

#include "nvEncodeAPI.h"

#pragma comment(lib, "d3d11.lib")

#define CHECK(call)                                                              \
    do {                                                                         \
        NVENCSTATUS s_ = (call);                                                 \
        if (s_ != NV_ENC_SUCCESS) {                                              \
            std::fprintf(stderr, "%s failed: %d (line %d)\n", #call, s_, __LINE__); \
            return 2;                                                            \
        }                                                                        \
    } while (0)

struct Args {
    int w = 2560, h = 1440, fps = 72, mbps = 200, frames = 432, preset = 4, noise = 8, ring = 8;
    std::string tuning = "hq", codec = "mvhevc", input = "sys";
    bool tenBit = false;
    std::string out;
};

static Args parse(int argc, char** argv) {
    Args a;
    for (int i = 1; i + 1 < argc || (i < argc && std::string(argv[i]) == "--10bit"); ++i) {
        std::string k = argv[i];
        if (k == "--10bit") { a.tenBit = true; continue; }
        std::string v = argv[++i];
        if (k == "--w") a.w = std::stoi(v);
        else if (k == "--h") a.h = std::stoi(v);
        else if (k == "--fps") a.fps = std::stoi(v);
        else if (k == "--mbps") a.mbps = std::stoi(v);
        else if (k == "--frames") a.frames = std::stoi(v);
        else if (k == "--preset") a.preset = std::stoi(v);
        else if (k == "--noise") a.noise = std::stoi(v);
        else if (k == "--out") a.out = v;
        else if (k == "--tuning") a.tuning = v;
        else if (k == "--codec") a.codec = v;
        else if (k == "--input") a.input = v;
        else if (k == "--ring") a.ring = std::stoi(v);
    }
    return a;
}

static NV_ENC_TUNING_INFO tuningInfo(const std::string& t) {
    if (t == "ll") return NV_ENC_TUNING_INFO_LOW_LATENCY;
    if (t == "ull") return NV_ENC_TUNING_INFO_ULTRA_LOW_LATENCY;
    return NV_ENC_TUNING_INFO_HIGH_QUALITY;
}

static const GUID* presetGuid(int p) {
    static const GUID* g[] = {&NV_ENC_PRESET_P1_GUID, &NV_ENC_PRESET_P2_GUID, &NV_ENC_PRESET_P3_GUID,
                              &NV_ENC_PRESET_P4_GUID, &NV_ENC_PRESET_P5_GUID, &NV_ENC_PRESET_P6_GUID,
                              &NV_ENC_PRESET_P7_GUID};
    return g[std::clamp(p, 1, 7) - 1];
}

// NV12 frame for one eye into (luma, chroma) planes. texture: coarse 8 px blocks, width 2*w for scrolling.
static void fillView(uint8_t* luma, uint8_t* chroma, uint32_t pitch, int w, int h,
                     const std::vector<uint8_t>& texture, int texW, int scroll, int disparity, int noise,
                     std::mt19937& rng) {
    std::uniform_int_distribution<int> n(-noise, noise);
    for (int y = 0; y < h; ++y) {
        uint8_t* row = luma + size_t(y) * pitch;
        const uint8_t* src = texture.data() + size_t(y) * texW;
        for (int x = 0; x < w; ++x) {
            int v = src[(x + scroll + disparity) % texW];
            if (noise) v = std::clamp(v + n(rng), 16, 235);
            row[x] = uint8_t(v);
        }
    }
    for (int y = 0; y < h / 2; ++y) {
        uint8_t* row = chroma + size_t(y) * pitch;
        for (int x = 0; x < w; x += 2) {
            int t = texture[size_t(y * 2) * texW + (x + scroll + disparity) % texW];
            row[x] = uint8_t(96 + t / 4);        // U
            row[x + 1] = uint8_t(160 - t / 4);   // V
        }
    }
}

static double nowMs() {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

int main(int argc, char** argv) {
    Args a = parse(argc, argv);
    const bool mv = a.codec == "mvhevc", h264 = a.codec == "h264", d3d = a.input == "d3d11";
    const int views = mv ? 2 : 1;
    const GUID codecGuid = h264 ? NV_ENC_CODEC_H264_GUID : NV_ENC_CODEC_HEVC_GUID;

    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION,
                                 &device, nullptr, &context))) {
        std::fprintf(stderr, "D3D11CreateDevice failed\n");
        return 1;
    }
    HMODULE lib = LoadLibraryA("nvEncodeAPI64.dll");
    if (!lib) { std::fprintf(stderr, "nvEncodeAPI64.dll not found\n"); return 1; }
    using CreateFn = NVENCSTATUS(NVENCAPI*)(NV_ENCODE_API_FUNCTION_LIST*);
    auto create = reinterpret_cast<CreateFn>(GetProcAddress(lib, "NvEncodeAPICreateInstance"));
    NV_ENCODE_API_FUNCTION_LIST nv = {NV_ENCODE_API_FUNCTION_LIST_VER};
    CHECK(create(&nv));

    NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS open = {NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER};
    open.device = device;
    open.deviceType = NV_ENC_DEVICE_TYPE_DIRECTX;
    open.apiVersion = NVENCAPI_VERSION;
    void* enc = nullptr;
    CHECK(nv.nvEncOpenEncodeSessionEx(&open, &enc));

    if (mv) {
        NV_ENC_CAPS_PARAM caps = {NV_ENC_CAPS_PARAM_VER};
        caps.capsToQuery = NV_ENC_CAPS_SUPPORT_MVHEVC_ENCODE;
        int mvhevc = 0;
        CHECK(nv.nvEncGetEncodeCaps(enc, NV_ENC_CODEC_HEVC_GUID, &caps, &mvhevc));
        if (!mvhevc) { std::fprintf(stderr, "NV_ENC_CAPS_SUPPORT_MVHEVC_ENCODE = 0\n"); return 3; }
    }

    NV_ENC_PRESET_CONFIG preset = {};
    preset.version = NV_ENC_PRESET_CONFIG_VER;
    preset.presetCfg.version = NV_ENC_CONFIG_VER;
    CHECK(nv.nvEncGetEncodePresetConfigEx(enc, codecGuid, *presetGuid(a.preset), tuningInfo(a.tuning), &preset));
    NV_ENC_CONFIG cfg = preset.presetCfg;
    cfg.gopLength = a.fps;
    cfg.frameIntervalP = 1;
    cfg.rcParams.rateControlMode = NV_ENC_PARAMS_RC_CBR;
    cfg.rcParams.averageBitRate = uint32_t(a.mbps) * 1000000u;
    cfg.rcParams.maxBitRate = cfg.rcParams.averageBitRate;
    cfg.rcParams.vbvBufferSize = cfg.rcParams.averageBitRate / a.fps;   // ~one frame
    cfg.rcParams.vbvInitialDelay = cfg.rcParams.vbvBufferSize;
    if (h264) {
        auto& c = cfg.encodeCodecConfig.h264Config;
        c.entropyCodingMode = NV_ENC_H264_ENTROPY_CODING_MODE_CAVLC;   // the headset decoder caps CABAC ~355 Mbps
        c.repeatSPSPPS = 1;
        c.idrPeriod = cfg.gopLength;
    } else {
        auto& c = cfg.encodeCodecConfig.hevcConfig;
        c.repeatSPSPPS = 1;
        c.outputAUD = 0;                 // a layer-0 AUD before view 1 would split the stereo AU
        c.idrPeriod = cfg.gopLength;
        if (mv) {
            c.enableMVHEVC = 1;
            c.numViews = 2;
            c.outputHevc3DReferenceDisplayInfo = 1;
        }
        if (a.tenBit) {
            c.inputBitDepth = NV_ENC_BIT_DEPTH_8;
            c.outputBitDepth = NV_ENC_BIT_DEPTH_10;
            cfg.profileGUID = NV_ENC_HEVC_PROFILE_MAIN10_GUID;
        }
    }

    NV_ENC_INITIALIZE_PARAMS init = {NV_ENC_INITIALIZE_PARAMS_VER};
    init.encodeGUID = codecGuid;
    init.presetGUID = *presetGuid(a.preset);
    init.tuningInfo = tuningInfo(a.tuning);
    init.encodeWidth = init.darWidth = init.maxEncodeWidth = a.w;
    init.encodeHeight = init.darHeight = init.maxEncodeHeight = a.h;
    init.frameRateNum = a.fps;
    init.frameRateDen = 1;
    init.enablePTD = 1;
    init.encodeConfig = &cfg;
    CHECK(nv.nvEncInitializeEncoder(enc, &init));

    // Per view: an NVENC system-memory buffer, or a D3D11 NV12 texture (+ CPU staging copy) registered once.
    // Probe mode (no --out): encode a small ring of pre-filled frames, paced at the frame rate, so the
    // GPU sees a steady stream like a real streamer does. Clip mode (--out): fill every frame, no pacing.
    const bool probe = a.out.empty();
    const int ring = probe ? std::max(1, a.ring) : 1;
    std::vector<std::vector<NV_ENC_INPUT_PTR>> sysInput(ring, std::vector<NV_ENC_INPUT_PTR>(views, nullptr));
    std::vector<std::vector<ID3D11Texture2D*>> gpuTex(ring, std::vector<ID3D11Texture2D*>(views, nullptr));
    std::vector<std::vector<NV_ENC_REGISTERED_PTR>> registered(ring, std::vector<NV_ENC_REGISTERED_PTR>(views, nullptr));
    ID3D11Texture2D* staging = nullptr;
    NV_ENC_OUTPUT_PTR output[2] = {};
    if (d3d) {
        D3D11_TEXTURE2D_DESC td = {};
        td.Width = a.w; td.Height = a.h; td.MipLevels = 1; td.ArraySize = 1;
        td.Format = DXGI_FORMAT_NV12; td.SampleDesc.Count = 1;
        td.Usage = D3D11_USAGE_STAGING; td.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        if (FAILED(device->CreateTexture2D(&td, nullptr, &staging))) { std::fprintf(stderr, "staging texture\n"); return 1; }
    }
    for (int r = 0; r < ring; ++r) {
        for (int v = 0; v < views; ++v) {
            if (d3d) {
                D3D11_TEXTURE2D_DESC td = {};
                td.Width = a.w; td.Height = a.h; td.MipLevels = 1; td.ArraySize = 1;
                td.Format = DXGI_FORMAT_NV12; td.SampleDesc.Count = 1;
                td.Usage = D3D11_USAGE_DEFAULT; td.BindFlags = D3D11_BIND_RENDER_TARGET;
                if (FAILED(device->CreateTexture2D(&td, nullptr, &gpuTex[r][v]))) { std::fprintf(stderr, "NV12 texture\n"); return 1; }
                NV_ENC_REGISTER_RESOURCE rr = {NV_ENC_REGISTER_RESOURCE_VER};
                rr.resourceType = NV_ENC_INPUT_RESOURCE_TYPE_DIRECTX;
                rr.resourceToRegister = gpuTex[r][v];
                rr.width = a.w; rr.height = a.h; rr.bufferFormat = NV_ENC_BUFFER_FORMAT_NV12;
                rr.bufferUsage = NV_ENC_INPUT_IMAGE;
                CHECK(nv.nvEncRegisterResource(enc, &rr));
                registered[r][v] = rr.registeredResource;
            } else {
                NV_ENC_CREATE_INPUT_BUFFER ib = {NV_ENC_CREATE_INPUT_BUFFER_VER};
                ib.width = a.w; ib.height = a.h; ib.bufferFmt = NV_ENC_BUFFER_FORMAT_NV12;
                CHECK(nv.nvEncCreateInputBuffer(enc, &ib));
                sysInput[r][v] = ib.inputBuffer;
            }
        }
    }
    for (int v = 0; v < views; ++v) {
        NV_ENC_CREATE_BITSTREAM_BUFFER bb = {NV_ENC_CREATE_BITSTREAM_BUFFER_VER};
        CHECK(nv.nvEncCreateBitstreamBuffer(enc, &bb));
        output[v] = bb.bitstreamBuffer;
    }
    ID3D11Query* done = nullptr;
    D3D11_QUERY_DESC qd = {D3D11_QUERY_EVENT, 0};
    device->CreateQuery(&qd, &done);

    int texW = a.w * 2;
    std::vector<uint8_t> texture(size_t(texW) * a.h);
    std::mt19937 rng(1234);
    std::uniform_int_distribution<int> tex(40, 215);
    for (int y = 0; y < a.h; y += 8)
        for (int x = 0; x < texW; x += 8) {
            uint8_t v = uint8_t(tex(rng));
            for (int dy = 0; dy < 8 && y + dy < a.h; ++dy)
                std::memset(&texture[size_t(y + dy) * texW + x], v, std::min(8, texW - x));
        }

    FILE* out = nullptr;
    if (!probe && !(out = std::fopen(a.out.c_str(), "wb"))) {
        std::fprintf(stderr, "cannot open %s\n", a.out.c_str());
        return 1;
    }
    const int disparity = a.w / 100;                    // ~1% of the width, like a mid-depth scene
    // Writes frame `f`'s content for view `v` into ring slot `r`.
    auto fill_slot = [&](int r, int f, int v) -> bool {
        const int scroll = (f * 5) % texW, shift = v ? disparity : 0;
        if (d3d) {
            D3D11_MAPPED_SUBRESOURCE m;
            if (FAILED(context->Map(staging, 0, D3D11_MAP_WRITE, 0, &m))) { std::fprintf(stderr, "Map\n"); return false; }
            uint8_t* base = static_cast<uint8_t*>(m.pData);
            fillView(base, base + size_t(m.RowPitch) * a.h, m.RowPitch, a.w, a.h, texture, texW, scroll, shift,
                     a.noise, rng);
            context->Unmap(staging, 0);
            context->CopyResource(gpuTex[r][v], staging);
        } else {
            NV_ENC_LOCK_INPUT_BUFFER lock = {NV_ENC_LOCK_INPUT_BUFFER_VER};
            lock.inputBuffer = sysInput[r][v];
            if (nv.nvEncLockInputBuffer(enc, &lock) != NV_ENC_SUCCESS) return false;
            uint8_t* base = static_cast<uint8_t*>(lock.bufferDataPtr);
            fillView(base, base + size_t(lock.pitch) * a.h, lock.pitch, a.w, a.h, texture, texW, scroll, shift,
                     a.noise, rng);
            nv.nvEncUnlockInputBuffer(enc, sysInput[r][v]);
        }
        return true;
    };
    if (probe) {                                         // pre-fill the ring; content repeats every `ring` frames
        for (int r = 0; r < ring; ++r)
            for (int v = 0; v < views; ++v)
                if (!fill_slot(r, r, v)) return 1;
    }
    if (d3d) {                                           // uploads complete before any timing starts
        context->End(done);
        while (context->GetData(done, nullptr, 0, 0) == S_FALSE) { }
    }

    std::vector<double> ms;
    std::vector<uint8_t> au;
    size_t totalBytes = 0;
    size_t viewBytes[2] = {0, 0};
    const double startMs = nowMs();
    for (int f = 0; f < a.frames; ++f) {
        au.clear();
        const int slot = probe ? f % ring : 0;
        if (!probe) {
            for (int v = 0; v < views; ++v)
                if (!fill_slot(0, f, v)) return 1;
            if (d3d) {
                context->End(done);
                while (context->GetData(done, nullptr, 0, 0) == S_FALSE) { }
            }
        } else {                                         // pace at the frame rate, like a live streamer
            const double due = startMs + f * 1000.0 / a.fps;
            while (nowMs() < due) { }
        }
        NV_ENC_MAP_INPUT_RESOURCE mapped[2] = {};
        for (int v = 0; v < views && d3d; ++v) {
            mapped[v] = {NV_ENC_MAP_INPUT_RESOURCE_VER};
            mapped[v].registeredResource = registered[slot][v];
            CHECK(nv.nvEncMapInputResource(enc, &mapped[v]));
        }

        double t0 = nowMs();
        for (int v = 0; v < views; ++v) {
            NV_ENC_PIC_PARAMS pic = {NV_ENC_PIC_PARAMS_VER};
            pic.inputWidth = a.w; pic.inputHeight = a.h;
            pic.inputBuffer = d3d ? mapped[v].mappedResource : sysInput[slot][v];
            pic.outputBitstream = output[v];
            pic.bufferFmt = NV_ENC_BUFFER_FORMAT_NV12;
            pic.pictureStruct = NV_ENC_PIC_STRUCT_FRAME;
            pic.inputTimeStamp = uint64_t(f);
            if (mv) pic.codecPicParams.hevcPicParams.viewId = v;
            NVENCSTATUS s = nv.nvEncEncodePicture(enc, &pic);
            if (s != NV_ENC_SUCCESS && s != NV_ENC_ERR_NEED_MORE_INPUT) {
                std::fprintf(stderr, "nvEncEncodePicture(view %d, frame %d) failed: %d\n", v, f, s);
                return 2;
            }
        }
        for (int v = 0; v < views; ++v) {                // synchronous: every view ready now
            NV_ENC_LOCK_BITSTREAM bs = {NV_ENC_LOCK_BITSTREAM_VER};
            bs.outputBitstream = output[v];
            CHECK(nv.nvEncLockBitstream(enc, &bs));
            const uint8_t* p = static_cast<const uint8_t*>(bs.bitstreamBufferPtr);
            au.insert(au.end(), p, p + bs.bitstreamSizeInBytes);
            viewBytes[v] += bs.bitstreamSizeInBytes;
            CHECK(nv.nvEncUnlockBitstream(enc, output[v]));
        }
        ms.push_back(nowMs() - t0);
        for (int v = 0; v < views && d3d; ++v) CHECK(nv.nvEncUnmapInputResource(enc, mapped[v].mappedResource));

        if (out) {
            uint32_t len = uint32_t(au.size());
            uint8_t be[4] = {uint8_t(len >> 24), uint8_t(len >> 16), uint8_t(len >> 8), uint8_t(len)};
            std::fwrite(be, 1, 4, out);
            std::fwrite(au.data(), 1, au.size(), out);
        }
        totalBytes += au.size();
    }
    if (out) std::fclose(out);
    ms.erase(ms.begin(), ms.begin() + std::min<size_t>(10, ms.size() - 1));   // warm-up
    std::sort(ms.begin(), ms.end());
    auto pct = [&](double q) { return ms[std::min(ms.size() - 1, size_t(ms.size() * q))]; };
    std::printf("encoded %d frames %s %dx%d%s @%d fps, %s, P%d/%s, %s input, target %d Mbps%s: actual %.1f Mbps\n",
                a.frames, a.codec.c_str(), a.w, a.h, mv ? "/eye" : "", a.fps, a.tenBit ? "10-bit" : "8-bit",
                a.preset, a.tuning.c_str(), a.input.c_str(), a.mbps, mv ? "/view" : "",
                totalBytes * 8.0 * a.fps / a.frames / 1e6);
    if (mv)
        std::printf("view split: base %.1f%%, second view %.1f%%\n", 100.0 * viewBytes[0] / totalBytes,
                    100.0 * viewBytes[1] / totalBytes);
    std::printf("encode time per frame: p50 %.2f ms, p95 %.2f ms, p99 %.2f ms, max %.2f ms\n", pct(0.5), pct(0.95),
                pct(0.99), ms.back());
    for (int r = 0; r < ring; ++r)
        for (int v = 0; v < views; ++v)
            if (registered[r][v]) nv.nvEncUnregisterResource(enc, registered[r][v]);
    nv.nvEncDestroyEncoder(enc);
    device->Release();
    return 0;
}
