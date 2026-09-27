// Dump the NVENC capabilities this GPU actually reports, per codec.
//
// Several numbers this project has been treating as facts were never measured. The 4096 H.264
// width limit that caps the foveation centre at 0.35 is an assumption inherited from a comment;
// NV_ENC_CAPS_SUPPORT_SUBFRAME_READBACK decides whether slices can be transmitted as they are
// encoded rather than a whole frame at a time, which is latency ALVR currently spends; and
// MV-HEVC support was only ever queried for HEVC.
//
// Build: build.cmd nvenc_caps   (after copying alongside mvhevc_clip.cpp)
#include <cstdio>
#include <cstring>
#include <windows.h>
#include <d3d11.h>
#include "nvEncodeAPI.h"

#pragma comment(lib, "d3d11.lib")

static NV_ENCODE_API_FUNCTION_LIST nv{};

static int cap(void* enc, GUID codec, NV_ENC_CAPS which) {
    NV_ENC_CAPS_PARAM p{};
    p.version = NV_ENC_CAPS_PARAM_VER;
    p.capsToQuery = which;
    int value = 0;
    if (nv.nvEncGetEncodeCaps(enc, codec, &p, &value) != NV_ENC_SUCCESS) return -1;
    return value;
}

struct Entry { const char* name; NV_ENC_CAPS id; };

int main() {
    ID3D11Device* dev = nullptr; ID3D11DeviceContext* ctx = nullptr;
    if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                 D3D11_SDK_VERSION, &dev, nullptr, &ctx))) {
        std::fprintf(stderr, "D3D11CreateDevice failed\n"); return 1;
    }
    // Loaded dynamically rather than linked: there is no import library alongside the header here,
    // and this is how mvhevc_clip.cpp already reaches NVENC.
    HMODULE lib = LoadLibraryA("nvEncodeAPI64.dll");
    if (!lib) { std::fprintf(stderr, "nvEncodeAPI64.dll not found\n"); return 2; }
    using CreateFn = NVENCSTATUS(NVENCAPI*)(NV_ENCODE_API_FUNCTION_LIST*);
    auto create = reinterpret_cast<CreateFn>(GetProcAddress(lib, "NvEncodeAPICreateInstance"));
    if (!create) { std::fprintf(stderr, "NvEncodeAPICreateInstance not exported\n"); return 2; }
    nv.version = NV_ENCODE_API_FUNCTION_LIST_VER;
    if (create(&nv) != NV_ENC_SUCCESS) {
        std::fprintf(stderr, "NvEncodeAPICreateInstance failed\n"); return 2;
    }
    NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS o{};
    o.version = NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER;
    o.deviceType = NV_ENC_DEVICE_TYPE_DIRECTX;
    o.device = dev;
    o.apiVersion = NVENCAPI_VERSION;
    void* enc = nullptr;
    if (nv.nvEncOpenEncodeSessionEx(&o, &enc) != NV_ENC_SUCCESS) {
        std::fprintf(stderr, "nvEncOpenEncodeSessionEx failed\n"); return 3;
    }

    std::printf("NVENC API %u.%u\n\n", NVENCAPI_MAJOR_VERSION, NVENCAPI_MINOR_VERSION);

    const Entry entries[] = {
        {"WIDTH_MAX",                 NV_ENC_CAPS_WIDTH_MAX},
        {"HEIGHT_MAX",                NV_ENC_CAPS_HEIGHT_MAX},
        {"SUPPORT_SUBFRAME_READBACK", NV_ENC_CAPS_SUPPORT_SUBFRAME_READBACK},
        {"SUPPORT_MVHEVC_ENCODE",     NV_ENC_CAPS_SUPPORT_MVHEVC_ENCODE},
        {"SUPPORT_LOOKAHEAD",         NV_ENC_CAPS_SUPPORT_LOOKAHEAD},
        {"SUPPORT_TEMPORAL_FILTER",   NV_ENC_CAPS_SUPPORT_TEMPORAL_FILTER},
        {"SUPPORT_TEMPORAL_AQ",       NV_ENC_CAPS_SUPPORT_TEMPORAL_AQ},
        {"SUPPORT_DYN_BITRATE_CHANGE",NV_ENC_CAPS_SUPPORT_DYN_BITRATE_CHANGE},
        {"SUPPORT_10BIT_ENCODE",      NV_ENC_CAPS_SUPPORT_10BIT_ENCODE},
        {"NUM_ENCODER_ENGINES",       NV_ENC_CAPS_NUM_ENCODER_ENGINES},
    };

    struct { const char* name; GUID guid; } codecs[] = {
        {"H264", NV_ENC_CODEC_H264_GUID},
        {"HEVC", NV_ENC_CODEC_HEVC_GUID},
        {"AV1",  NV_ENC_CODEC_AV1_GUID},
    };

    for (auto& c : codecs) {
        std::printf("=== %s ===\n", c.name);
        for (auto& e : entries) {
            int v = cap(enc, c.guid, e.id);
            if (v < 0) std::printf("  %-28s unsupported/query-failed\n", e.name);
            else       std::printf("  %-28s %d\n", e.name, v);
        }
        std::printf("\n");
    }

    nv.nvEncDestroyEncoder(enc);
    ctx->Release(); dev->Release();
    return 0;
}
