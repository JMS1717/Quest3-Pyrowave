// driver_xrwired: a SteamVR driver that presents the Galaxy XR as a directly-driven headset and
// streams what SteamVR renders to our own client on the headset.
//
// SteamVR path: the driver advertises an HMD with no real display, so the compositor renders into
// textures we hand it (IVRDriverDirectModeComponent). On Present we copy both eyes into one
// side-by-side frame, encode it with NVENC and put it on the wire - no window, no desktop mirror,
// and the pixels never leave the GPU until they are already compressed.
//
// This is the PC half of the pair; the headset half is receiver-xr (com.xrwired.receiverxr), which
// renders each frame as an OpenXR projection layer and reports back when it was decoded and shown.
//
// Configuration: xrwired.cfg beside the DLL, "key=value" per line (width, height, fps, mbps, port,
// codec, preset, tuning, ipd, fov_tan). Defaults match the measured sweet spot: 3264x1408 at 72 fps,
// 400 Mbps H.264 CAVLC.
#define NOMINMAX
#include <d3d11.h>
#include <openvr_driver.h>
#include <windows.h>

#include <atomic>
#include <chrono>
#include <cstring>
#include <fstream>
#include <map>
#include <condition_variable>
#include <mutex>
#include <algorithm>
#include <cmath>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <d3dcompiler.h>

#include "encoder.h"
#include "stream.h"

#pragma comment(lib, "d3dcompiler.lib")

using namespace vr;

namespace {

std::string module_directory();

void log(const std::string& text) {
    OutputDebugStringA(("[xrwired] " + text + "\n").c_str());
    // vrserver is launched by SteamVR, so an env var would not reach us: log beside the DLL.
    static const std::string path = module_directory() + "\\driver_xrwired.log";
    std::ofstream file(path, std::ios::app);
    file << text << "\n";
}

struct Config {
    int width = 3264, height = 1408, fps = 72, mbps = 400, port = 45100, preset = 1;
    float render_scale = 1.0f;                // SteamVR renders this multiple of the encoded size
    bool hevc = false, ten_bit = false;
    std::string tuning = "ll";
    std::string client_ip;                    // empty: wait for the headset to connect to us
    std::string pattern_file;                 // raw RGBA frame to stream instead of SteamVR's image
    std::string dump_file;                    // where to drop one encoded keyframe, for inspection
    float ipd = 0.063f;
    // Galaxy XR's measured left-eye tangents (the right eye mirrors them), used until the headset
    // connects and reports its own: SteamVR only asks GetProjectionRaw once, at startup.
    float fov_left = -1.40134f, fov_right = 0.83541f, fov_up = 1.30649f, fov_down = -1.30649f;
};

// What the headset last told us: where its head is and what it can see. The pose id travels back
// out as the frame's timestamp, so the client knows which pose each image was rendered for.
struct PoseState {
    std::atomic<uint64_t> id{0};
    std::mutex mutex;
    std::condition_variable arrived;          // signalled when a new pose id lands
    HmdQuaternion_t orientation = {1, 0, 0, 0};
    double position[3] = {0, 1.6, 0};
    double linear_velocity[3] = {};           // SteamVR extrapolates poses with these
    double angular_velocity[3] = {};
    float fov[8] = {};                        // left eye l,r,u,d then right eye; zero until reported
    bool have_fov = false;

    // Every pose we handed to SteamVR, so a rendered frame can be matched back to the pose it was
    // actually rendered from - SteamVR samples a pose when it starts a frame, not when it presents it.
    std::map<uint64_t, HmdQuaternion_t> history;

    void remember(uint64_t pose_id, const HmdQuaternion_t& rotation) {
        history[pose_id] = rotation;
        while (history.size() > 256) history.erase(history.begin());
    }

    /** The id of the remembered pose closest to `rotation`, or 0 when nothing matches. */
    uint64_t best_match(const HmdQuaternion_t& rotation) const {
        uint64_t best = 0;
        double best_distance = 1e9;
        for (const auto& entry : history) {
            const HmdQuaternion_t& q = entry.second;
            const double distance = std::abs(q.w - rotation.w) + std::abs(q.x - rotation.x) +
                                    std::abs(q.y - rotation.y) + std::abs(q.z - rotation.z);
            if (distance < best_distance) {
                best_distance = distance;
                best = entry.first;
            }
        }
        return best_distance < 0.01 ? best : 0;
    }
};

/** Rotation part of a SteamVR pose matrix as a quaternion. */
HmdQuaternion_t matrix_rotation(const HmdMatrix34_t& m) {
    HmdQuaternion_t q;
    q.w = std::sqrt(std::max(0.0, 1.0 + m.m[0][0] + m.m[1][1] + m.m[2][2])) / 2.0;
    q.x = std::sqrt(std::max(0.0, 1.0 + m.m[0][0] - m.m[1][1] - m.m[2][2])) / 2.0;
    q.y = std::sqrt(std::max(0.0, 1.0 - m.m[0][0] + m.m[1][1] - m.m[2][2])) / 2.0;
    q.z = std::sqrt(std::max(0.0, 1.0 - m.m[0][0] - m.m[1][1] + m.m[2][2])) / 2.0;
    q.x = std::copysign(q.x, double(m.m[2][1] - m.m[1][2]));
    q.y = std::copysign(q.y, double(m.m[0][2] - m.m[2][0]));
    q.z = std::copysign(q.z, double(m.m[1][0] - m.m[0][1]));
    return q;
}

std::string module_directory() {
    HMODULE module = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCSTR>(&module_directory), &module);
    char path[MAX_PATH] = {};
    GetModuleFileNameA(module, path, MAX_PATH);
    std::string full(path);
    return full.substr(0, full.find_last_of('\\'));
}

Config load_config() {
    Config config;
    std::ifstream file(module_directory() + "\\xrwired.cfg");
    std::string line;
    while (std::getline(file, line)) {
        const size_t split = line.find('=');
        if (line.empty() || line[0] == '#' || split == std::string::npos) continue;
        const std::string key = line.substr(0, split), value = line.substr(split + 1);
        if (key == "width") config.width = std::stoi(value);
        else if (key == "height") config.height = std::stoi(value);
        else if (key == "fps") config.fps = std::stoi(value);
        else if (key == "mbps") config.mbps = std::stoi(value);
        else if (key == "port") config.port = std::stoi(value);
        else if (key == "preset") config.preset = std::stoi(value);
        else if (key == "codec") { config.hevc = value.rfind("hevc", 0) == 0; config.ten_bit = value == "hevc10"; }
        else if (key == "tuning") config.tuning = value;
        else if (key == "ipd") config.ipd = std::stof(value);
        else if (key == "fov_left") config.fov_left = std::stof(value);
        else if (key == "fov_right") config.fov_right = std::stof(value);
        else if (key == "fov_up") config.fov_up = std::stof(value);
        else if (key == "fov_down") config.fov_down = std::stof(value);
        else if (key == "client_ip") config.client_ip = value;
        else if (key == "pattern_file") config.pattern_file = value;
        else if (key == "dump_file") config.dump_file = value;
        else if (key == "render_scale") config.render_scale = std::stof(value);
    }
    std::ostringstream summary;
    summary << "config " << config.width << "x" << config.height << "@" << config.fps << " " << config.mbps
            << " Mbps " << (config.hevc ? (config.ten_bit ? "hevc10" : "hevc") : "h264") << " port " << config.port;
    log(summary.str());
    return config;
}

// SteamVR renders each eye larger than we asked for (its own supersampling), so the eye images are
// scaled - not cropped - into the side-by-side frame we encode. That downscale is free supersampling.
class Blitter {
public:
    bool init(ID3D11Device* device) {
        static const char* kShader = R"(
struct VsOut { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
cbuffer Region : register(b0) { float2 uv_offset; float2 uv_scale; };
VsOut vs(uint id : SV_VertexID) {
    float2 corner = float2((id << 1) & 2, id & 2);       // (0,0) (2,0) (0,2)
    VsOut o;
    o.pos = float4(corner * float2(2, -2) + float2(-1, 1), 0, 1);
    o.uv = uv_offset + corner * uv_scale;
    return o;
}
Texture2D source : register(t0);
SamplerState linear_clamp : register(s0);
float4 ps(VsOut i) : SV_TARGET { return source.Sample(linear_clamp, i.uv); }
)";
        ID3DBlob* vs_blob = nullptr;
        ID3DBlob* ps_blob = nullptr;
        ID3DBlob* errors = nullptr;
        if (FAILED(D3DCompile(kShader, strlen(kShader), nullptr, nullptr, nullptr, "vs", "vs_4_0", 0, 0,
                              &vs_blob, &errors)) ||
            FAILED(D3DCompile(kShader, strlen(kShader), nullptr, nullptr, nullptr, "ps", "ps_4_0", 0, 0,
                              &ps_blob, &errors))) {
            log(std::string("blit shader failed: ") +
                (errors != nullptr ? static_cast<const char*>(errors->GetBufferPointer()) : "?"));
            return false;
        }
        device->CreateVertexShader(vs_blob->GetBufferPointer(), vs_blob->GetBufferSize(), nullptr, &vs_);
        device->CreatePixelShader(ps_blob->GetBufferPointer(), ps_blob->GetBufferSize(), nullptr, &ps_);
        vs_blob->Release();
        ps_blob->Release();

        D3D11_SAMPLER_DESC sampler = {};
        sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
        sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        device->CreateSamplerState(&sampler, &sampler_);

        D3D11_BUFFER_DESC constants = {};
        constants.ByteWidth = 16;
        constants.Usage = D3D11_USAGE_DYNAMIC;
        constants.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        constants.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        device->CreateBuffer(&constants, nullptr, &constants_);
        return vs_ != nullptr && ps_ != nullptr && sampler_ != nullptr && constants_ != nullptr;
    }

    /** Draws the `bounds` part of `source` into the given viewport of the render target. */
    void blit(ID3D11Device* device, ID3D11DeviceContext* context, ID3D11Texture2D* source,
              const VRTextureBounds_t& bounds, ID3D11RenderTargetView* target, float x, float y,
              float width, float height) {
        ID3D11ShaderResourceView* view = shader_view(device, source);
        if (view == nullptr) return;
        D3D11_MAPPED_SUBRESOURCE mapped = {};
        if (SUCCEEDED(context->Map(constants_, 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped))) {
            const float region[4] = {bounds.uMin, bounds.vMin, bounds.uMax - bounds.uMin,
                                     bounds.vMax - bounds.vMin};
            memcpy(mapped.pData, region, sizeof(region));
            context->Unmap(constants_, 0);
        }
        D3D11_VIEWPORT viewport = {x, y, width, height, 0.0f, 1.0f};
        context->OMSetRenderTargets(1, &target, nullptr);
        context->RSSetViewports(1, &viewport);
        context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        context->IASetInputLayout(nullptr);
        context->VSSetShader(vs_, nullptr, 0);
        context->VSSetConstantBuffers(0, 1, &constants_);
        context->PSSetShader(ps_, nullptr, 0);
        context->PSSetShaderResources(0, 1, &view);
        context->PSSetSamplers(0, 1, &sampler_);
        context->OMSetBlendState(nullptr, nullptr, 0xffffffff);
        context->OMSetDepthStencilState(nullptr, 0);
        context->RSSetState(nullptr);
        context->Draw(3, 0);
        ID3D11ShaderResourceView* none = nullptr;
        context->PSSetShaderResources(0, 1, &none);
        // NVENC will not register a texture that is still bound as a render target, so let it go.
        ID3D11RenderTargetView* no_target = nullptr;
        context->OMSetRenderTargets(1, &no_target, nullptr);
    }

    void release() {
        for (auto& entry : views_) entry.second->Release();
        views_.clear();
        if (vs_ != nullptr) vs_->Release();
        if (ps_ != nullptr) ps_->Release();
        if (sampler_ != nullptr) sampler_->Release();
        if (constants_ != nullptr) constants_->Release();
        vs_ = nullptr;
        ps_ = nullptr;
        sampler_ = nullptr;
        constants_ = nullptr;
    }

private:
    ID3D11ShaderResourceView* shader_view(ID3D11Device* device, ID3D11Texture2D* texture) {
        auto cached = views_.find(texture);
        if (cached != views_.end()) return cached->second;
        D3D11_TEXTURE2D_DESC desc = {};
        texture->GetDesc(&desc);
        D3D11_SHADER_RESOURCE_VIEW_DESC view_desc = {};
        view_desc.Format = desc.Format == DXGI_FORMAT_R8G8B8A8_TYPELESS ? DXGI_FORMAT_R8G8B8A8_UNORM
                                                                        : desc.Format;
        view_desc.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        view_desc.Texture2D.MipLevels = 1;
        ID3D11ShaderResourceView* view = nullptr;
        if (FAILED(device->CreateShaderResourceView(texture, &view_desc, &view))) {
            log("CreateShaderResourceView failed");
            return nullptr;
        }
        views_[texture] = view;
        return view;
    }

    ID3D11VertexShader* vs_ = nullptr;
    ID3D11PixelShader* ps_ = nullptr;
    ID3D11SamplerState* sampler_ = nullptr;
    ID3D11Buffer* constants_ = nullptr;
    std::map<ID3D11Texture2D*, ID3D11ShaderResourceView*> views_;
};

// One process's swap texture set: SteamVR renders into these and hands the handles back on submit.
struct TextureSet {
    uint32_t pid = 0;
    ID3D11Texture2D* textures[3] = {};
    HANDLE handles[3] = {};
};

class DirectMode : public IVRDriverDirectModeComponent {
public:
    DirectMode(ID3D11Device* device, ID3D11DeviceContext* context, const Config& config,
               xrwired::Encoder* encoder, xrwired::Stream* stream, PoseState* pose)
        : device_(device), context_(context), config_(config), encoder_(encoder), stream_(stream),
          pose_(pose) { }

    void CreateSwapTextureSet(uint32_t pid, const SwapTextureSetDesc_t* desc,
                              SwapTextureSet_t* out) override {
        D3D11_TEXTURE2D_DESC texture_desc = {};
        const DXGI_FORMAT format = static_cast<DXGI_FORMAT>(desc->nFormat);
        texture_desc.Width = desc->nWidth;
        texture_desc.Height = desc->nHeight;
        texture_desc.MipLevels = 1;
        texture_desc.ArraySize = 1;
        texture_desc.Format = format;
        texture_desc.SampleDesc.Count = desc->nSampleCount == 0 ? 1 : desc->nSampleCount;
        texture_desc.Usage = D3D11_USAGE_DEFAULT;
        texture_desc.BindFlags = (format == DXGI_FORMAT_R32G8X24_TYPELESS || format == DXGI_FORMAT_R32_TYPELESS)
                                     ? D3D11_BIND_DEPTH_STENCIL
                                     : D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
        texture_desc.MiscFlags = D3D11_RESOURCE_MISC_SHARED;

        std::lock_guard<std::mutex> lock(mutex_);
        TextureSet* set = new TextureSet();
        set->pid = pid;
        for (int i = 0; i < 3; ++i) {
            if (FAILED(device_->CreateTexture2D(&texture_desc, nullptr, &set->textures[i]))) {
                log("CreateTexture2D failed");
                delete set;
                return;
            }
            IDXGIResource* resource = nullptr;
            if (FAILED(set->textures[i]->QueryInterface(__uuidof(IDXGIResource), reinterpret_cast<void**>(&resource))) ||
                FAILED(resource->GetSharedHandle(&set->handles[i]))) {
                log("GetSharedHandle failed");
                if (resource != nullptr) resource->Release();
                delete set;
                return;
            }
            resource->Release();
            sets_[set->handles[i]] = {set, i};
            out->rSharedTextureHandles[i] = reinterpret_cast<SharedTextureHandle_t>(set->handles[i]);
        }
        std::ostringstream text;
        text << "swap texture set pid=" << pid << " " << desc->nWidth << "x" << desc->nHeight
             << " format=" << desc->nFormat;
        log(text.str());
    }

    void DestroySwapTextureSet(SharedTextureHandle_t handle) override {
        std::lock_guard<std::mutex> lock(mutex_);
        auto found = sets_.find(reinterpret_cast<HANDLE>(handle));
        if (found == sets_.end()) return;
        TextureSet* set = found->second.first;
        for (HANDLE h : set->handles) sets_.erase(h);
        for (ID3D11Texture2D* texture : set->textures) {
            if (texture != nullptr) texture->Release();
        }
        delete set;
    }

    void DestroyAllSwapTextureSets(uint32_t pid) override {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto it = sets_.begin(); it != sets_.end();) {
            TextureSet* set = it->second.first;
            if (set->pid != pid) {
                ++it;
                continue;
            }
            const bool last = it->second.second == 2;
            it = sets_.erase(it);
            if (last) {
                for (ID3D11Texture2D* texture : set->textures) {
                    if (texture != nullptr) texture->Release();
                }
                delete set;
            }
        }
    }

    void GetNextSwapTextureSetIndex(SharedTextureHandle_t[2], uint32_t (*indices)[2]) override {
        (*indices)[0] = ((*indices)[0] + 1) % 3;
        (*indices)[1] = ((*indices)[1] + 1) % 3;
    }

    void SubmitLayer(const SubmitLayerPerEye_t (&per_eye)[2]) override {
        std::lock_guard<std::mutex> lock(mutex_);
        if (layers_ == 0) {                                   // only the first (scene) layer is streamed
            submitted_[0] = per_eye[0];
            submitted_[1] = per_eye[1];
            // Which pose did SteamVR render this from? Match its own pose matrix against what we sent.
            const HmdQuaternion_t rendered = matrix_rotation(per_eye[0].mHmdPose);
            std::lock_guard<std::mutex> pose_lock(pose_->mutex);
            const uint64_t matched = pose_->best_match(rendered);
            frame_pose_id_ = matched != 0 ? matched : pose_->id.load();
        }
        layers_++;
    }

    void Present(SharedTextureHandle_t sync_texture) override {
        std::lock_guard<std::mutex> lock(mutex_);
        const int layers = layers_;
        layers_ = 0;
        if (layers == 0 || targets_[0] == nullptr) return;

        // The compositor is still writing to the submitted textures until we take the sync texture's
        // keyed mutex; everything between Acquire and Release is ours.
        ID3D11Texture2D* sync = shared_texture(reinterpret_cast<HANDLE>(sync_texture));
        IDXGIKeyedMutex* keyed = nullptr;
        if (sync != nullptr &&
            SUCCEEDED(sync->QueryInterface(__uuidof(IDXGIKeyedMutex), reinterpret_cast<void**>(&keyed)))) {
            if (keyed->AcquireSync(0, 10) != S_OK) {
                keyed->Release();
                return;
            }
        }

        if (pattern_ != nullptr) {                     // measurement mode: stream a known image
            context_->CopyResource(targets_[target_index_], pattern_);
        } else {
        const float eye_width = float(config_.width / 2);
        for (int eye = 0; eye < 2; ++eye) {
            ID3D11Texture2D* eye_texture = shared_texture(reinterpret_cast<HANDLE>(submitted_[eye].hTexture));
            if (eye_texture == nullptr) continue;
            if (!warned_size_) {
                D3D11_TEXTURE2D_DESC desc = {};
                eye_texture->GetDesc(&desc);
                std::ostringstream text;
                text << "submitted eye " << desc.Width << "x" << desc.Height << " -> scaled into "
                     << int(eye_width) << "x" << config_.height;
                log(text.str());
                warned_size_ = true;
            }
            blitter_->blit(device_, context_, eye_texture, submitted_[eye].bounds, views_[target_index_],
                           eye == 0 ? 0.0f : eye_width, 0.0f, eye_width, float(config_.height));
        }
        }

        if (keyed != nullptr) {
            keyed->ReleaseSync(0);
            keyed->Release();
        }

        // Stamp the frame with the pose it was rendered for (falling back to a frame counter before
        // the headset has sent one), so the client can reproject against that exact pose.
        frames_++;
        const uint64_t pose_id = frame_pose_id_;
        const uint64_t pts_us = pose_id != 0 ? pose_id : uint64_t(frames_) * 1000000ull / uint64_t(config_.fps);
        // Hand the frame to the encoder thread: encoding here would stall the compositor for a whole
        // frame interval, which shows up as a lower frame rate and duplicated frames in the headset.
        {
            std::lock_guard<std::mutex> lock(queue_mutex_);
            pending_target_ = target_index_;
            pending_pts_ = pts_us;
            has_pending_ = true;
        }
        queue_ready_.notify_one();
        target_index_ = (target_index_ + 1) % kTargets;
    }

    /** Encoder thread: takes the newest frame Present left behind, encodes it and sends it. */
    void encode_loop() {
        std::vector<uint8_t> frame;
        while (encoding_) {
            int index = -1;
            uint64_t pts_us = 0;
            {
                std::unique_lock<std::mutex> lock(queue_mutex_);
                queue_ready_.wait(lock, [&] { return has_pending_ || !encoding_; });
                if (!encoding_) break;
                index = pending_target_;
                pts_us = pending_pts_;
                has_pending_ = false;
            }
            frame.clear();
            const bool idr = stream_->take_keyframe_request() || encoded_ == 0;
            if (!encoder_->encode(targets_[index], pts_us, idr, &frame)) {
                log("encode failed: " + encoder_->error());
                continue;
            }
            stream_->send_frame(frame.data(), frame.size(), pts_us);
            if (idr && !config_.dump_file.empty()) {      // one keyframe on disk to check what we send
                std::ofstream file(config_.dump_file, std::ios::binary | std::ios::trunc);
                file.write(reinterpret_cast<const char*>(frame.data()), std::streamsize(frame.size()));
                log("wrote " + std::to_string(frame.size()) + " bytes to " + config_.dump_file);
            }
            if (++encoded_ % 72 == 0) {
                const xrwired::StreamStats stats = stream_->stats();
                std::ostringstream text;
                text << "frames=" << frames_ << " encoded=" << encoded_ << " encode="
                     << encoder_->last_encode_ms() << " ms sent=" << stats.frames_sent
                     << " decoded_ms=" << stats.last_send_to_decoded_ms
                     << " photons_ms=" << stats.last_send_to_photons_ms;
                log(text.str());
            }
        }
    }

    void start_encoder_thread() {
        encoding_ = true;
        encoder_thread_ = std::thread(&DirectMode::encode_loop, this);
    }

    void stop_encoder_thread() {
        encoding_ = false;
        queue_ready_.notify_all();
        if (encoder_thread_.joinable()) encoder_thread_.join();
    }

    void PostPresent() override { }

    void GetFrameTiming(DriverDirectMode_FrameTiming* timing) override {
        if (timing != nullptr) timing->m_nNumFramePresents = 1;
    }

    /** The frames both eyes are scaled into (round-robin so the encoder thread reads a settled one). */
    void set_encode_targets(ID3D11Texture2D* const* textures, ID3D11RenderTargetView* const* views,
                            Blitter* blitter) {
        for (int i = 0; i < kTargets; ++i) {
            targets_[i] = textures[i];
            views_[i] = views[i];
        }
        blitter_ = blitter;
    }

    void set_test_pattern(ID3D11Texture2D* pattern) { pattern_ = pattern; }

    static constexpr int kTargets = 3;

private:
    ID3D11Texture2D* shared_texture(HANDLE handle) {
        if (handle == nullptr) return nullptr;
        auto cached = opened_.find(handle);
        if (cached != opened_.end()) return cached->second;
        ID3D11Texture2D* texture = nullptr;
        if (FAILED(device_->OpenSharedResource(handle, __uuidof(ID3D11Texture2D),
                                               reinterpret_cast<void**>(&texture)))) {
            return nullptr;
        }
        opened_[handle] = texture;
        return texture;
    }

    ID3D11Device* device_;
    ID3D11DeviceContext* context_;
    Config config_;
    xrwired::Encoder* encoder_;
    xrwired::Stream* stream_;
    PoseState* pose_;
    ID3D11Texture2D* pattern_ = nullptr;       // non-null in measurement mode
    ID3D11Texture2D* targets_[kTargets] = {};
    ID3D11RenderTargetView* views_[kTargets] = {};
    Blitter* blitter_ = nullptr;
    int target_index_ = 0;

    std::mutex queue_mutex_;
    std::condition_variable queue_ready_;
    std::thread encoder_thread_;
    std::atomic<bool> encoding_{false};
    bool has_pending_ = false;
    int pending_target_ = 0;
    uint64_t pending_pts_ = 0;
    uint64_t encoded_ = 0;          // reused: one encoded access unit
    std::mutex mutex_;
    std::map<HANDLE, std::pair<TextureSet*, int>> sets_;
    std::map<HANDLE, ID3D11Texture2D*> opened_;
    SubmitLayerPerEye_t submitted_[2] = {};
    int layers_ = 0;
    uint64_t frames_ = 0;
    uint64_t frame_pose_id_ = 0;              // the pose SteamVR rendered the pending frame from
    bool warned_size_ = false;
};

class Hmd : public ITrackedDeviceServerDriver, public IVRDisplayComponent {
public:
    Hmd(const Config& config, PoseState* pose) : config_(config), pose_(pose) { }

    EVRInitError Activate(uint32_t object_id) override {
        object_id_ = object_id;
        const PropertyContainerHandle_t container = VRProperties()->TrackedDeviceToPropertyContainer(object_id);
        VRProperties()->SetStringProperty(container, Prop_ModelNumber_String, "XR Wired HMD");
        VRProperties()->SetStringProperty(container, Prop_SerialNumber_String, "XRW-0001");
        VRProperties()->SetStringProperty(container, Prop_TrackingSystemName_String, "xrwired");
        VRProperties()->SetStringProperty(container, Prop_ManufacturerName_String, "XR Wired");
        VRProperties()->SetStringProperty(container, Prop_RenderModelName_String, "generic_hmd");
        VRProperties()->SetFloatProperty(container, Prop_UserIpdMeters_Float, config_.ipd);
        VRProperties()->SetFloatProperty(container, Prop_DisplayFrequency_Float, float(config_.fps));
        VRProperties()->SetFloatProperty(container, Prop_SecondsFromVsyncToPhotons_Float, 0.005f);
        VRProperties()->SetFloatProperty(container, Prop_UserHeadToEyeDepthMeters_Float, 0.0f);
        VRProperties()->SetUint64Property(container, Prop_CurrentUniverseId_Uint64, 2);
        VRProperties()->SetBoolProperty(container, Prop_IsOnDesktop_Bool, false);
        VRProperties()->SetBoolProperty(container, Prop_DisplayDebugMode_Bool, false);
        VRProperties()->SetBoolProperty(container, Prop_HasDriverDirectModeComponent_Bool, true);
        // We drive the frame cadence ourselves (one vsync per frame the headset asks for), rather than
        // letting the compositor render as fast as the GPU allows.
        VRProperties()->SetBoolProperty(container, Prop_DriverDirectModeSendsVsyncEvents_Bool, true);
        // Without a proximity sensor SteamVR assumes nobody is wearing the headset and stops rendering
        // after a while, which looks exactly like the stream dying.
        VRProperties()->SetBoolProperty(container, Prop_ContainsProximitySensor_Bool, true);
        VRDriverInput()->CreateBooleanComponent(container, "/proximity", &proximity_);
        set_worn(true);
        log("HMD activated");
        return VRInitError_None;
    }

    void Deactivate() override { object_id_ = k_unTrackedDeviceIndexInvalid; }
    void EnterStandby() override { }
    void DebugRequest(const char*, char* response, uint32_t size) override {
        if (size > 0) response[0] = 0;
    }

    void* GetComponent(const char* name) override {
        if (std::strcmp(name, IVRDisplayComponent_Version) == 0) return static_cast<IVRDisplayComponent*>(this);
        if (std::strcmp(name, IVRDriverDirectModeComponent_Version) == 0) return direct_mode_;
        return nullptr;
    }

    DriverPose_t GetPose() override {
        DriverPose_t pose = {};
        pose.poseIsValid = true;
        pose.result = TrackingResult_Running_OK;
        pose.deviceIsConnected = true;
        pose.qWorldFromDriverRotation.w = 1;
        pose.qDriverFromHeadRotation.w = 1;
        {
            std::lock_guard<std::mutex> lock(pose_->mutex);
            pose.qRotation = pose_->orientation;
            for (int i = 0; i < 3; ++i) {
                pose.vecPosition[i] = pose_->position[i];
                pose.vecVelocity[i] = pose_->linear_velocity[i];
                pose.vecAngularVelocity[i] = pose_->angular_velocity[i];
            }
        }
        return pose;
    }

    // IVRDisplayComponent: no desktop window and no real display, so SteamVR uses direct mode.
    void GetWindowBounds(int32_t* x, int32_t* y, uint32_t* width, uint32_t* height) override {
        *x = 0;
        *y = 0;
        *width = uint32_t(config_.width);
        *height = uint32_t(config_.height);
    }
    bool IsDisplayOnDesktop() override { return false; }
    bool IsDisplayRealDisplay() override { return false; }
    void GetRecommendedRenderTargetSize(uint32_t* width, uint32_t* height) override {
        // SteamVR multiplies this by its own quality factor, then we scale the result down into the
        // encoded frame. render_scale trades sharpness for GPU time when the compositor is the limit.
        *width = uint32_t(config_.width / 2 * config_.render_scale);
        *height = uint32_t(config_.height * config_.render_scale);
    }
    void GetEyeOutputViewport(EVREye eye, uint32_t* x, uint32_t* y, uint32_t* width,
                              uint32_t* height) override {
        *x = eye == Eye_Left ? 0 : uint32_t(config_.width / 2);
        *y = 0;
        *width = uint32_t(config_.width / 2);
        *height = uint32_t(config_.height);
    }
    void GetProjectionRaw(EVREye eye, float* left, float* right, float* top, float* bottom) override {
        std::lock_guard<std::mutex> lock(pose_->mutex);
        if (!pose_->have_fov) {                       // until the headset reports its own optics
            *left = eye == Eye_Left ? config_.fov_left : -config_.fov_right;
            *right = eye == Eye_Left ? config_.fov_right : -config_.fov_left;
            *top = config_.fov_down;
            *bottom = config_.fov_up;
            return;
        }
        const float* fov = pose_->fov + (eye == Eye_Left ? 0 : 4);   // angles: left, right, up, down
        *left = std::tan(fov[0]);
        *right = std::tan(fov[1]);
        *top = std::tan(fov[3]);                      // OpenVR's "top" is the down angle (negative)
        *bottom = std::tan(fov[2]);
    }
    DistortionCoordinates_t ComputeDistortion(EVREye, float u, float v) override {
        DistortionCoordinates_t coordinates = {};
        coordinates.rfRed[0] = coordinates.rfGreen[0] = coordinates.rfBlue[0] = u;
        coordinates.rfRed[1] = coordinates.rfGreen[1] = coordinates.rfBlue[1] = v;
        return coordinates;
    }

    /** Tells SteamVR the headset is on a head, which keeps the compositor rendering. */
    void set_worn(bool worn) {
        if (proximity_ != vr::k_ulInvalidInputComponentHandle) {
            VRDriverInput()->UpdateBooleanComponent(proximity_, worn, 0.0);
        }
    }

    void set_direct_mode(DirectMode* direct_mode) { direct_mode_ = direct_mode; }
    uint32_t object_id() const { return object_id_; }

private:
    Config config_;
    PoseState* pose_;
    DirectMode* direct_mode_ = nullptr;
    uint32_t object_id_ = k_unTrackedDeviceIndexInvalid;
    VRInputComponentHandle_t proximity_ = k_ulInvalidInputComponentHandle;
};

class Provider : public IServerTrackedDeviceProvider {
public:
    EVRInitError Init(IVRDriverContext* context) override {
        VR_INIT_SERVER_DRIVER_CONTEXT(context);
        config_ = load_config();

        const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0};
        if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, levels, 2,
                                     D3D11_SDK_VERSION, &device_, nullptr, &context_))) {
            log("D3D11CreateDevice failed");
            return VRInitError_Driver_Failed;
        }
        D3D11_TEXTURE2D_DESC desc = {};
        desc.Width = UINT(config_.width);
        desc.Height = UINT(config_.height);
        desc.MipLevels = desc.ArraySize = 1;
        desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        desc.SampleDesc.Count = 1;
        desc.Usage = D3D11_USAGE_DEFAULT;
        desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
        for (int i = 0; i < DirectMode::kTargets; ++i) {
            if (FAILED(device_->CreateTexture2D(&desc, nullptr, &encode_targets_[i])) ||
                FAILED(device_->CreateRenderTargetView(encode_targets_[i], nullptr, &target_views_[i]))) {
                log("encode target creation failed");
                return VRInitError_Driver_Failed;
            }
        }
        if (!blitter_.init(device_)) return VRInitError_Driver_Failed;

        xrwired::EncoderConfig encoder_config;
        encoder_config.width = config_.width;
        encoder_config.height = config_.height;
        encoder_config.fps = config_.fps;
        encoder_config.mbps = config_.mbps;
        encoder_config.hevc = config_.hevc;
        encoder_config.ten_bit = config_.ten_bit;
        encoder_config.preset = config_.preset;
        encoder_config.tuning = config_.tuning;
        if (!encoder_.init(device_, encoder_config)) {
            log("encoder init failed: " + encoder_.error());
            return VRInitError_Driver_Failed;
        }
        const int codec_id = config_.hevc ? (config_.ten_bit ? 4 : 3) : 0;
        const bool started = config_.client_ip.empty()
                                 ? stream_.listen(config_.port, config_.width, config_.height, config_.fps, codec_id)
                                 : stream_.connect_to(config_.client_ip, config_.port, config_.width, config_.height,
                                                      config_.fps, codec_id);
        if (!started) {
            log("stream setup failed");
            return VRInitError_Driver_Failed;
        }

        // The headset client listens; we dial it when an address is configured, otherwise we wait.
        stream_.on_pose([this](uint64_t id, const float values[21]) {
            bool fov_changed = false;
            {
                std::lock_guard<std::mutex> lock(pose_.mutex);
                pose_.orientation = {values[3], values[0], values[1], values[2]};   // w, x, y, z
                pose_.position[0] = values[4];
                pose_.position[1] = values[5];
                pose_.position[2] = values[6];
                fov_changed = std::memcmp(pose_.fov, values + 7, sizeof(pose_.fov)) != 0;
                std::memcpy(pose_.fov, values + 7, sizeof(pose_.fov));
                for (int i = 0; i < 3; ++i) {
                    pose_.linear_velocity[i] = values[15 + i];
                    pose_.angular_velocity[i] = values[18 + i];
                }
                pose_.have_fov = pose_.fov[1] != 0.0f;
            }
            {
                std::lock_guard<std::mutex> lock(pose_.mutex);
                pose_.remember(id, pose_.orientation);
            }
            pose_.id.store(id);
            pose_.arrived.notify_all();
            if (fov_changed && pose_.have_fov) publish_projection();
        });

        hmd_ = new Hmd(config_, &pose_);
        direct_mode_ = new DirectMode(device_, context_, config_, &encoder_, &stream_, &pose_);
        if (!config_.pattern_file.empty()) {
            std::ifstream file(config_.pattern_file, std::ios::binary);
            std::vector<uint8_t> pixels((std::istreambuf_iterator<char>(file)),
                                        std::istreambuf_iterator<char>());
            const size_t wanted = size_t(config_.width) * config_.height * 4;
            if (pixels.size() != wanted) {
                log("pattern file is " + std::to_string(pixels.size()) + " bytes, expected " +
                    std::to_string(wanted));
            } else {
                D3D11_SUBRESOURCE_DATA data = {pixels.data(), UINT(config_.width * 4), 0};
                D3D11_TEXTURE2D_DESC pattern_desc = desc;
                pattern_desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
                if (SUCCEEDED(device_->CreateTexture2D(&pattern_desc, &data, &pattern_))) {
                    direct_mode_->set_test_pattern(pattern_);
                    log("streaming the test pattern from " + config_.pattern_file);
                }
            }
        }
        direct_mode_->set_encode_targets(encode_targets_, target_views_, &blitter_);
        direct_mode_->start_encoder_thread();
        hmd_->set_direct_mode(direct_mode_);
        VRServerDriverHost()->TrackedDeviceAdded("XRW-0001", TrackedDeviceClass_HMD, hmd_);

        running_ = true;
        vsync_thread_ = std::thread([this] {
            uint64_t last_pose = 0;
            const auto interval = std::chrono::microseconds(1000000 / config_.fps);
            // Absolute deadlines, not "sleep for an interval": the per-tick overhead would otherwise
            // accumulate and we would pace a few percent slow, which shows up as duplicated frames.
            auto next = std::chrono::steady_clock::now() + interval;
            while (running_) {
                {   // the headset asking for a frame wins; the deadline is just the fallback
                    std::unique_lock<std::mutex> lock(pose_.mutex);
                    pose_.arrived.wait_until(lock, next, [&] { return pose_.id.load() != last_pose; });
                    last_pose = pose_.id.load();
                }
                if (hmd_ != nullptr && hmd_->object_id() != k_unTrackedDeviceIndexInvalid) {
                    VRServerDriverHost()->VsyncEvent(0.0);
                }
                const auto now = std::chrono::steady_clock::now();
                next += interval;
                if (next < now) next = now + interval;      // fell behind: resynchronise, do not spiral
            }
        });
        pose_thread_ = std::thread([this] {
            while (running_) {                       // SteamVR wants a steady pose feed
                if (hmd_->object_id() != k_unTrackedDeviceIndexInvalid) {
                    VRServerDriverHost()->TrackedDevicePoseUpdated(hmd_->object_id(), hmd_->GetPose(),
                                                                   sizeof(DriverPose_t));
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            }
        });
        log("driver ready");
        return VRInitError_None;
    }

    void Cleanup() override {
        running_ = false;
        pose_.arrived.notify_all();
        if (vsync_thread_.joinable()) vsync_thread_.join();
        if (pose_thread_.joinable()) pose_thread_.join();
        if (direct_mode_ != nullptr) direct_mode_->stop_encoder_thread();
        stream_.stop();
        encoder_.shutdown();
        blitter_.release();
        if (pattern_ != nullptr) pattern_->Release();
        for (int i = 0; i < DirectMode::kTargets; ++i) {
            if (target_views_[i] != nullptr) target_views_[i]->Release();
            if (encode_targets_[i] != nullptr) encode_targets_[i]->Release();
        }
        if (context_ != nullptr) context_->Release();
        if (device_ != nullptr) device_->Release();
        delete direct_mode_;
        delete hmd_;
        direct_mode_ = nullptr;
        hmd_ = nullptr;
        VR_CLEANUP_SERVER_DRIVER_CONTEXT();
    }

    /** SteamVR asks GetProjectionRaw once at startup, before the headset has told us its optics, so
     *  push the real per-eye field of view as soon as it arrives. */
    void publish_projection() {
        if (hmd_ == nullptr || hmd_->object_id() == k_unTrackedDeviceIndexInvalid) return;
        HmdRect2_t eyes[2];
        {
            std::lock_guard<std::mutex> lock(pose_.mutex);
            for (int eye = 0; eye < 2; ++eye) {
                const float* fov = pose_.fov + eye * 4;          // angles: left, right, up, down
                eyes[eye].vTopLeft.v[0] = std::tan(fov[0]);
                eyes[eye].vBottomRight.v[0] = std::tan(fov[1]);
                eyes[eye].vTopLeft.v[1] = std::tan(fov[3]);
                eyes[eye].vBottomRight.v[1] = std::tan(fov[2]);
            }
            std::ostringstream text;
            text << "headset fov: left " << eyes[0].vTopLeft.v[0] << "," << eyes[0].vBottomRight.v[0] << ","
                 << eyes[0].vTopLeft.v[1] << "," << eyes[0].vBottomRight.v[1] << "  right "
                 << eyes[1].vTopLeft.v[0] << "," << eyes[1].vBottomRight.v[0] << ","
                 << eyes[1].vTopLeft.v[1] << "," << eyes[1].vBottomRight.v[1]
                 << " -> eye aspect " << (eyes[0].vBottomRight.v[0] - eyes[0].vTopLeft.v[0]) /
                                          (eyes[0].vBottomRight.v[1] - eyes[0].vTopLeft.v[1]);
            log(text.str());
        }
        VRServerDriverHost()->SetDisplayProjectionRaw(hmd_->object_id(), eyes[0], eyes[1]);
        VRServerDriverHost()->VendorSpecificEvent(hmd_->object_id(), VREvent_LensDistortionChanged, {}, 0);
    }

    const char* const* GetInterfaceVersions() override { return k_InterfaceVersions; }
    void RunFrame() override { }
    /** A streaming headset is "in use" whenever the client is connected: letting SteamVR drop into
     *  standby stops the compositor, which from the headset looks exactly like the stream dying. */
    bool ShouldBlockStandbyMode() override { return true; }
    void EnterStandby() override { log("SteamVR asked for standby"); }
    void LeaveStandby() override { log("SteamVR left standby"); }

private:
    Config config_;
    ID3D11Device* device_ = nullptr;
    ID3D11DeviceContext* context_ = nullptr;
    ID3D11Texture2D* pattern_ = nullptr;
    ID3D11Texture2D* encode_targets_[DirectMode::kTargets] = {};
    ID3D11RenderTargetView* target_views_[DirectMode::kTargets] = {};
    Blitter blitter_;
    xrwired::Encoder encoder_;
    xrwired::Stream stream_;
    PoseState pose_;
    Hmd* hmd_ = nullptr;
    DirectMode* direct_mode_ = nullptr;
    std::atomic<bool> running_{false};
    std::thread pose_thread_;
    std::thread vsync_thread_;
};

Provider g_provider;

}  // namespace

extern "C" __declspec(dllexport) void* HmdDriverFactory(const char* interface_name, int* return_code) {
    if (std::strcmp(interface_name, IServerTrackedDeviceProvider_Version) == 0) return &g_provider;
    if (return_code != nullptr) *return_code = VRInitError_Init_InterfaceNotFound;
    return nullptr;
}
