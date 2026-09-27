// NVENC encoder for the SteamVR driver. See encoder.h for the contract.
//
// The driver hands us whatever SteamVR rendered, so the input is an RGBA/BGRA D3D11 texture, not NV12:
// we register it with NVENC as ARGB/ABGR and let the encoder do the colour conversion on the way in,
// which saves a conversion pass. A texture NVENC cannot register directly (wrong size, multisampled, or
// owned by another device) is copied into an internal texture with the description NVENC wants.
// Everything is synchronous: map, encode, lock, append, unlock, unmap, with one bitstream buffer reused
// for the life of the session. This runs inside a driver DLL, so failures go to error() and
// OutputDebugStringA, never to stdio.
#define NOMINMAX
#include "encoder.h"

#include <d3d11.h>
#include <dxgi.h>
#include <windows.h>

#include <algorithm>
#include <chrono>
#include <string>
#include <unordered_map>
#include <vector>

#include "nvEncodeAPI.h"

namespace xrwired {
namespace {

void log(const std::string& msg) {
    OutputDebugStringA(("[xrwired.encoder] " + msg + "\n").c_str());
}

std::string hr_string(HRESULT hr) {
    static const char* digits = "0123456789abcdef";
    std::string s = "0x";
    const uint32_t v = static_cast<uint32_t>(hr);
    for (int shift = 28; shift >= 0; shift -= 4) s += digits[(v >> shift) & 0xf];
    return s;
}

NV_ENC_TUNING_INFO tuning_info(const std::string& t) {
    if (t == "ull") return NV_ENC_TUNING_INFO_ULTRA_LOW_LATENCY;
    if (t == "hq") return NV_ENC_TUNING_INFO_HIGH_QUALITY;
    return NV_ENC_TUNING_INFO_LOW_LATENCY;  // "ll" and anything unrecognised
}

const GUID& preset_guid(int p) {
    static const GUID* g[] = {&NV_ENC_PRESET_P1_GUID, &NV_ENC_PRESET_P2_GUID, &NV_ENC_PRESET_P3_GUID,
                              &NV_ENC_PRESET_P4_GUID, &NV_ENC_PRESET_P5_GUID, &NV_ENC_PRESET_P6_GUID,
                              &NV_ENC_PRESET_P7_GUID};
    return *g[std::clamp(p, 1, 7) - 1];
}

// NVENC's packed formats are word-ordered, so a D3D R8G8B8A8 texture (byte order R,G,B,A) is ABGR.
bool nvenc_format(DXGI_FORMAT dxgi, NV_ENC_BUFFER_FORMAT* out) {
    switch (dxgi) {
        case DXGI_FORMAT_R8G8B8A8_UNORM:
        case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
        case DXGI_FORMAT_R8G8B8A8_TYPELESS:
            *out = NV_ENC_BUFFER_FORMAT_ABGR;
            return true;
        case DXGI_FORMAT_B8G8R8A8_UNORM:
        case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
        case DXGI_FORMAT_B8G8R8A8_TYPELESS:
        case DXGI_FORMAT_B8G8R8X8_UNORM:
            *out = NV_ENC_BUFFER_FORMAT_ARGB;
            return true;
        default:
            return false;
    }
}

// The typeless variants cannot be created as an NVENC input, so pin them to the matching UNORM.
DXGI_FORMAT internal_format(DXGI_FORMAT dxgi) {
    switch (dxgi) {
        case DXGI_FORMAT_R8G8B8A8_TYPELESS:
        case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
            return DXGI_FORMAT_R8G8B8A8_UNORM;
        case DXGI_FORMAT_B8G8R8A8_TYPELESS:
        case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
        case DXGI_FORMAT_B8G8R8X8_UNORM:
            return DXGI_FORMAT_B8G8R8A8_UNORM;
        default:
            return dxgi;
    }
}

}  // namespace

struct Encoder::Impl {
    HMODULE lib = nullptr;
    NV_ENCODE_API_FUNCTION_LIST nv = {};
    void* enc = nullptr;
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    EncoderConfig cfg;
    NV_ENC_OUTPUT_PTR bitstream = nullptr;

    // Registered NVENC inputs, keyed by the texture the caller passed in. A streamer hands us the same
    // few textures over and over, so each one registers once and is mapped/unmapped per frame.
    std::unordered_map<ID3D11Texture2D*, NV_ENC_REGISTERED_PTR> registered;
    // Textures opened from another device's shared handle, keyed by that device's texture pointer.
    std::unordered_map<ID3D11Texture2D*, ID3D11Texture2D*> opened;
    // Landing pad for inputs NVENC cannot take directly.
    ID3D11Texture2D* scratch = nullptr;
    DXGI_FORMAT scratch_format = DXGI_FORMAT_UNKNOWN;
};

Encoder::~Encoder() {
    shutdown();
}

bool Encoder::init(ID3D11Device* device, const EncoderConfig& config) {
    shutdown();
    error_.clear();

    auto fail = [this](const std::string& msg) {
        error_ = msg;
        log("init: " + msg);
        shutdown();
        return false;
    };

    if (!device) return fail("no D3D11 device");
    if (config.width <= 0 || config.height <= 0 || config.fps <= 0 || config.mbps <= 0)
        return fail("bad config: " + std::to_string(config.width) + "x" + std::to_string(config.height) + " @" +
                    std::to_string(config.fps) + " " + std::to_string(config.mbps) + " Mbps");

    impl_ = new Impl();
    impl_->cfg = config;
    impl_->device = device;
    device->AddRef();
    device->GetImmediateContext(&impl_->context);

    impl_->lib = LoadLibraryA("nvEncodeAPI64.dll");
    if (!impl_->lib) return fail("nvEncodeAPI64.dll not found (no NVIDIA driver?)");
    using CreateFn = NVENCSTATUS(NVENCAPI*)(NV_ENCODE_API_FUNCTION_LIST*);
    auto create = reinterpret_cast<CreateFn>(GetProcAddress(impl_->lib, "NvEncodeAPICreateInstance"));
    if (!create) return fail("NvEncodeAPICreateInstance missing from nvEncodeAPI64.dll");
    impl_->nv.version = NV_ENCODE_API_FUNCTION_LIST_VER;
    NVENCSTATUS s = create(&impl_->nv);
    if (s != NV_ENC_SUCCESS) return fail("NvEncodeAPICreateInstance failed: " + std::to_string(static_cast<int>(s)));

    NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS open = {};
    open.version = NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER;
    open.device = device;
    open.deviceType = NV_ENC_DEVICE_TYPE_DIRECTX;
    open.apiVersion = NVENCAPI_VERSION;
    s = impl_->nv.nvEncOpenEncodeSessionEx(&open, &impl_->enc);
    if (s != NV_ENC_SUCCESS) {
        impl_->enc = nullptr;
        return fail("nvEncOpenEncodeSessionEx failed: " + std::to_string(static_cast<int>(s)));
    }

    const GUID codec = config.hevc ? NV_ENC_CODEC_HEVC_GUID : NV_ENC_CODEC_H264_GUID;
    const NV_ENC_TUNING_INFO tuning = tuning_info(config.tuning);

    // NV_ENC_PRESET_CONFIG has a `reserved` member, so set the versions by hand rather than brace-initialising.
    NV_ENC_PRESET_CONFIG preset = {};
    preset.version = NV_ENC_PRESET_CONFIG_VER;
    preset.presetCfg.version = NV_ENC_CONFIG_VER;
    s = impl_->nv.nvEncGetEncodePresetConfigEx(impl_->enc, codec, preset_guid(config.preset), tuning, &preset);
    if (s != NV_ENC_SUCCESS) return fail("nvEncGetEncodePresetConfigEx failed: " + std::to_string(static_cast<int>(s)));

    NV_ENC_CONFIG cfg = preset.presetCfg;
    cfg.version = NV_ENC_CONFIG_VER;
    cfg.gopLength = NVENC_INFINITE_GOPLENGTH;   // IDR only when the client asks for one
    cfg.frameIntervalP = 1;                     // IPP..., no B frames
    cfg.rcParams.rateControlMode = NV_ENC_PARAMS_RC_CBR;
    cfg.rcParams.averageBitRate = static_cast<uint32_t>(config.mbps) * 1000000u;
    cfg.rcParams.maxBitRate = cfg.rcParams.averageBitRate;
    cfg.rcParams.vbvBufferSize = cfg.rcParams.averageBitRate / static_cast<uint32_t>(config.fps);  // one frame
    cfg.rcParams.vbvInitialDelay = cfg.rcParams.vbvBufferSize;

    if (!config.hevc) {
        NV_ENC_CONFIG_H264& h264 = cfg.encodeCodecConfig.h264Config;
        h264.entropyCodingMode = NV_ENC_H264_ENTROPY_CODING_MODE_CAVLC;  // headset decoder caps CABAC ~355 Mbps
        h264.repeatSPSPPS = 1;
        h264.idrPeriod = NVENC_INFINITE_GOPLENGTH;
        h264.outputAUD = 0;
        if (config.ten_bit) log("init: ten_bit ignored, H.264 path is 8-bit");
    } else {
        NV_ENC_CONFIG_HEVC& hevc = cfg.encodeCodecConfig.hevcConfig;
        hevc.repeatSPSPPS = 1;
        hevc.idrPeriod = NVENC_INFINITE_GOPLENGTH;
        hevc.outputAUD = 0;
        if (config.ten_bit) {
            hevc.inputBitDepth = NV_ENC_BIT_DEPTH_8;   // packed 8-bit RGBA in
            hevc.outputBitDepth = NV_ENC_BIT_DEPTH_10;
            cfg.profileGUID = NV_ENC_HEVC_PROFILE_MAIN10_GUID;
        }
    }

    NV_ENC_INITIALIZE_PARAMS init = {};
    init.version = NV_ENC_INITIALIZE_PARAMS_VER;
    init.encodeGUID = codec;
    init.presetGUID = preset_guid(config.preset);
    init.tuningInfo = tuning;
    init.encodeWidth = init.darWidth = init.maxEncodeWidth = static_cast<uint32_t>(config.width);
    init.encodeHeight = init.darHeight = init.maxEncodeHeight = static_cast<uint32_t>(config.height);
    init.frameRateNum = static_cast<uint32_t>(config.fps);
    init.frameRateDen = 1;
    init.enablePTD = 1;
    init.enableEncodeAsync = 0;   // synchronous: nvEncLockBitstream blocks until the frame is done
    init.encodeConfig = &cfg;
    s = impl_->nv.nvEncInitializeEncoder(impl_->enc, &init);
    if (s != NV_ENC_SUCCESS) return fail("nvEncInitializeEncoder failed: " + std::to_string(static_cast<int>(s)));

    NV_ENC_CREATE_BITSTREAM_BUFFER bb = {};
    bb.version = NV_ENC_CREATE_BITSTREAM_BUFFER_VER;
    s = impl_->nv.nvEncCreateBitstreamBuffer(impl_->enc, &bb);
    if (s != NV_ENC_SUCCESS) return fail("nvEncCreateBitstreamBuffer failed: " + std::to_string(static_cast<int>(s)));
    impl_->bitstream = bb.bitstreamBuffer;

    log("init: " + std::string(config.hevc ? "HEVC" : "H.264 CAVLC") + " " + std::to_string(config.width) + "x" +
        std::to_string(config.height) + " @" + std::to_string(config.fps) + " CBR " + std::to_string(config.mbps) +
        " Mbps, P" + std::to_string(std::clamp(config.preset, 1, 7)) + "/" + config.tuning +
        (config.hevc && config.ten_bit ? ", 10-bit" : ""));
    return true;
}

bool Encoder::encode(ID3D11Texture2D* texture, uint64_t pts_us, bool force_idr, std::vector<uint8_t>* out) {
    error_.clear();
    auto fail = [this](const std::string& msg) {
        error_ = msg;
        log("encode: " + msg);
        return false;
    };
    if (!impl_ || !impl_->enc) return fail("encoder not initialised");
    if (!texture) return fail("no input texture");
    if (!out) return fail("no output vector");

    Impl& im = *impl_;
    D3D11_TEXTURE2D_DESC desc = {};
    texture->GetDesc(&desc);

    NV_ENC_BUFFER_FORMAT fmt;
    if (!nvenc_format(desc.Format, &fmt))
        return fail("unsupported input format DXGI_FORMAT " + std::to_string(static_cast<int>(desc.Format)) +
                    "; need R8G8B8A8_UNORM(_SRGB) or B8G8R8A8_UNORM(_SRGB)");

    // Is this texture something NVENC can register as-is?
    ID3D11Device* owner = nullptr;
    texture->GetDevice(&owner);
    const bool same_device = owner == im.device;
    if (owner) owner->Release();
    const bool right_size = static_cast<int>(desc.Width) == im.cfg.width &&
                            static_cast<int>(desc.Height) == im.cfg.height;
    const bool single_sample = desc.SampleDesc.Count == 1;
    const bool renderable = (desc.BindFlags & D3D11_BIND_RENDER_TARGET) != 0;
    const bool direct = same_device && right_size && single_sample && renderable;

    ID3D11Texture2D* input = texture;
    if (!direct) {
        // Copy into an internal texture with the description NVENC accepts.
        ID3D11Texture2D* src = texture;
        if (!same_device) {
            auto it = im.opened.find(texture);
            if (it != im.opened.end()) {
                src = it->second;
            } else {
                if (!(desc.MiscFlags & (D3D11_RESOURCE_MISC_SHARED | D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX)))
                    return fail("input texture belongs to another device and is not shared");
                IDXGIResource* dxgi = nullptr;
                HRESULT hr = texture->QueryInterface(__uuidof(IDXGIResource), reinterpret_cast<void**>(&dxgi));
                if (FAILED(hr)) return fail("QueryInterface(IDXGIResource) failed: " + hr_string(hr));
                HANDLE shared = nullptr;
                hr = dxgi->GetSharedHandle(&shared);
                dxgi->Release();
                if (FAILED(hr)) return fail("GetSharedHandle failed: " + hr_string(hr));
                ID3D11Texture2D* opened = nullptr;
                hr = im.device->OpenSharedResource(shared, __uuidof(ID3D11Texture2D),
                                                   reinterpret_cast<void**>(&opened));
                if (FAILED(hr)) return fail("OpenSharedResource failed: " + hr_string(hr));
                im.opened[texture] = opened;
                src = opened;
                log("encode: opened a shared texture from another device");
            }
        }

        const DXGI_FORMAT want = internal_format(desc.Format);
        if (im.scratch && im.scratch_format != want) {
            auto it = im.registered.find(im.scratch);
            if (it != im.registered.end()) {
                im.nv.nvEncUnregisterResource(im.enc, it->second);
                im.registered.erase(it);
            }
            im.scratch->Release();
            im.scratch = nullptr;
        }
        if (!im.scratch) {
            D3D11_TEXTURE2D_DESC td = {};
            td.Width = static_cast<UINT>(im.cfg.width);
            td.Height = static_cast<UINT>(im.cfg.height);
            td.MipLevels = 1;
            td.ArraySize = 1;
            td.Format = want;
            td.SampleDesc.Count = 1;
            td.Usage = D3D11_USAGE_DEFAULT;
            td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
            HRESULT hr = im.device->CreateTexture2D(&td, nullptr, &im.scratch);
            if (FAILED(hr)) return fail("CreateTexture2D(internal input) failed: " + hr_string(hr));
            im.scratch_format = want;
            log("encode: using an internal " + std::to_string(im.cfg.width) + "x" + std::to_string(im.cfg.height) +
                " copy of the caller's texture (size/samples/device mismatch)");
        }

        if (!single_sample) {
            if (!right_size) return fail("multisampled input must already be the encode size");
            im.context->ResolveSubresource(im.scratch, 0, src, 0, want);
        } else if (right_size) {
            im.context->CopyResource(im.scratch, src);
        } else {
            D3D11_BOX box = {};
            box.right = std::min<UINT>(desc.Width, static_cast<UINT>(im.cfg.width));
            box.bottom = std::min<UINT>(desc.Height, static_cast<UINT>(im.cfg.height));
            box.back = 1;
            im.context->CopySubresourceRegion(im.scratch, 0, 0, 0, 0, src, 0, &box);
        }
        input = im.scratch;
        nvenc_format(want, &fmt);
    }

    NV_ENC_REGISTERED_PTR reg = nullptr;
    auto it = im.registered.find(input);
    if (it != im.registered.end()) {
        reg = it->second;
    } else {
        NV_ENC_REGISTER_RESOURCE rr = {};
        rr.version = NV_ENC_REGISTER_RESOURCE_VER;
        rr.resourceType = NV_ENC_INPUT_RESOURCE_TYPE_DIRECTX;
        rr.resourceToRegister = input;
        rr.width = static_cast<uint32_t>(im.cfg.width);
        rr.height = static_cast<uint32_t>(im.cfg.height);
        rr.bufferFormat = fmt;
        rr.bufferUsage = NV_ENC_INPUT_IMAGE;
        NVENCSTATUS s = im.nv.nvEncRegisterResource(im.enc, &rr);
        if (s != NV_ENC_SUCCESS) return fail("nvEncRegisterResource failed: " + std::to_string(static_cast<int>(s)));
        reg = rr.registeredResource;
        im.registered[input] = reg;
    }

    NV_ENC_MAP_INPUT_RESOURCE mapped = {};
    mapped.version = NV_ENC_MAP_INPUT_RESOURCE_VER;
    mapped.registeredResource = reg;
    NVENCSTATUS s = im.nv.nvEncMapInputResource(im.enc, &mapped);
    if (s != NV_ENC_SUCCESS) return fail("nvEncMapInputResource failed: " + std::to_string(static_cast<int>(s)));

    const auto t0 = std::chrono::steady_clock::now();

    NV_ENC_PIC_PARAMS pic = {};
    pic.version = NV_ENC_PIC_PARAMS_VER;
    pic.inputWidth = static_cast<uint32_t>(im.cfg.width);
    pic.inputHeight = static_cast<uint32_t>(im.cfg.height);
    pic.inputBuffer = mapped.mappedResource;
    pic.outputBitstream = im.bitstream;
    pic.bufferFmt = mapped.mappedBufferFmt;
    pic.pictureStruct = NV_ENC_PIC_STRUCT_FRAME;
    pic.inputTimeStamp = pts_us;
    if (force_idr) {
        pic.pictureType = NV_ENC_PIC_TYPE_IDR;
        pic.encodePicFlags = NV_ENC_PIC_FLAG_FORCEIDR | NV_ENC_PIC_FLAG_OUTPUT_SPSPPS;
    }
    s = im.nv.nvEncEncodePicture(im.enc, &pic);
    if (s != NV_ENC_SUCCESS) {
        im.nv.nvEncUnmapInputResource(im.enc, mapped.mappedResource);
        return fail("nvEncEncodePicture failed: " + std::to_string(static_cast<int>(s)));
    }

    NV_ENC_LOCK_BITSTREAM bs = {};
    bs.version = NV_ENC_LOCK_BITSTREAM_VER;
    bs.outputBitstream = im.bitstream;
    s = im.nv.nvEncLockBitstream(im.enc, &bs);
    if (s != NV_ENC_SUCCESS) {
        im.nv.nvEncUnmapInputResource(im.enc, mapped.mappedResource);
        return fail("nvEncLockBitstream failed: " + std::to_string(static_cast<int>(s)));
    }
    const uint8_t* p = static_cast<const uint8_t*>(bs.bitstreamBufferPtr);
    out->insert(out->end(), p, p + bs.bitstreamSizeInBytes);
    s = im.nv.nvEncUnlockBitstream(im.enc, im.bitstream);

    last_encode_ms_ = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();

    im.nv.nvEncUnmapInputResource(im.enc, mapped.mappedResource);
    if (s != NV_ENC_SUCCESS) return fail("nvEncUnlockBitstream failed: " + std::to_string(static_cast<int>(s)));
    return true;
}

void Encoder::shutdown() {
    if (!impl_) return;
    Impl& im = *impl_;
    if (im.enc) {
        for (auto& kv : im.registered) im.nv.nvEncUnregisterResource(im.enc, kv.second);
        if (im.bitstream) im.nv.nvEncDestroyBitstreamBuffer(im.enc, im.bitstream);
        im.nv.nvEncDestroyEncoder(im.enc);
    }
    im.registered.clear();
    for (auto& kv : im.opened) kv.second->Release();
    im.opened.clear();
    if (im.scratch) im.scratch->Release();
    if (im.context) im.context->Release();
    if (im.device) im.device->Release();
    if (im.lib) FreeLibrary(im.lib);
    delete impl_;
    impl_ = nullptr;
}

}  // namespace xrwired
