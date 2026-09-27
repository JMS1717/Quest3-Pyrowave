// xrwired_runtime — our own PC-side OpenXR runtime.
//
// M0: loader negotiation + instance/system surface.
// M1 (this file): a real D3D11 session — the app renders into swapchain textures we own, and on
//     xrEndFrame we scale both eyes into one side-by-side frame, encode it with NVENC and stream it
//     to the Galaxy XR client (receiver-xr), reusing encoder.cpp + stream.cpp + the Blitter. Head
//     pose/fov come back from the headset over the same socket and drive xrLocateViews. No SteamVR.
//
// The runtime exports one symbol, xrNegotiateLoaderRuntimeInterface; all else flows through the
// xrGetInstanceProcAddr it returns. Single instance/session (all a streaming runtime needs at once).
#define _CRT_SECURE_NO_WARNINGS
#define XR_USE_GRAPHICS_API_D3D11
#include <d3d11.h>
#include <windows.h>

#include <atomic>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <openxr/openxr_loader_negotiation.h>

#include "blitter.h"
#include "encoder.h"     // ../driver/src (added to the include path by build.cmd)
#include "stream.h"
#include "log.h"

using xrw::log;
using xrwired::Encoder;
using xrwired::EncoderConfig;
using xrwired::Stream;
using xrwired::StreamStats;

namespace {

constexpr uint64_t kInstanceHandle = 0x9001;
constexpr uint64_t kSystemId = 0x5001;

struct Config {
    std::string headset_ip = "192.0.2.10";
    int port = 45100, fps = 72, mbps = 400;
    uint32_t eye_w = 1424, eye_h = 1664;
    std::string codec = "h264";     // h264 | hevc | hevc10
    float ipd = 0.063f;
};
Config g_cfg;

std::string module_dir() {
    HMODULE m = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCSTR>(&module_dir), &m);
    char p[MAX_PATH] = {};
    GetModuleFileNameA(m, p, MAX_PATH);
    std::string f(p);
    return f.substr(0, f.find_last_of('\\'));
}

void load_config() {
    std::ifstream file(module_dir() + "\\xrwired.cfg");
    std::string line;
    while (std::getline(file, line)) {
        auto eq = line.find('=');
        if (line.empty() || line[0] == '#' || eq == std::string::npos) continue;
        std::string k = line.substr(0, eq), v = line.substr(eq + 1);
        if (k == "headset_ip") g_cfg.headset_ip = v;
        else if (k == "port") g_cfg.port = std::stoi(v);
        else if (k == "fps") g_cfg.fps = std::stoi(v);
        else if (k == "mbps") g_cfg.mbps = std::stoi(v);
        else if (k == "eye_w") g_cfg.eye_w = std::stoul(v);
        else if (k == "eye_h") g_cfg.eye_h = std::stoul(v);
        else if (k == "codec") g_cfg.codec = v;
        else if (k == "ipd") g_cfg.ipd = std::stof(v);
    }
}

// ---- session-wide state ------------------------------------------------------------------------

struct Swapchain {
    std::vector<ID3D11Texture2D*> images;
    uint32_t width = 0, height = 0;
    uint32_t acquired = 0, released = 0;
};

struct Space {
    XrReferenceSpaceType type = XR_REFERENCE_SPACE_TYPE_LOCAL;
    bool is_action = false;
};

struct HeadPose {
    std::mutex mutex;
    std::atomic<uint64_t> id{0};
    XrQuaternionf orientation{0, 0, 0, 1};
    XrVector3f position{0, 0, 0};
    float fov[8] = {-0.9f, 0.9f, 0.9f, -0.9f, -0.9f, 0.9f, 0.9f, -0.9f};  // L l,r,u,d then R
    bool have_fov = false;
};

struct HandsState {
    std::mutex mutex;
    bool active[2] = {false, false};
    float joint[2][26 * 8] = {};   // px,py,pz, qx,qy,qz,qw, radius per joint (XrHandJointEXT order)
};

struct Runtime {
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;   // app's immediate context

    // A D3D11 device we own, used only by the encoder thread for NVENC. The app's device stays touched
    // by the app's render thread alone (the blit), and the encoder reads our shared ring targets across
    // to this device. Sharing one device between the render thread and NVENC on the worker makes
    // nvEncMapInputResource fail (INVALID_PARAM); the SteamVR driver dodges it the same way.
    ID3D11Device* enc_device = nullptr;
    ID3D11DeviceContext* enc_context = nullptr;

    Encoder encoder;
    Blitter blitter;
    Stream stream;

    // Encoding runs on its own thread so NVENC never stalls the app's frame loop (a full-frame encode
    // is ~10-15 ms and would otherwise cap the render rate). xrEndFrame blits both eyes into the next
    // ring target and hands its index off; the encoder thread reads a target the render loop has
    // already moved past, so no two threads touch the same texture. Mirrors the SteamVR driver.
    static constexpr int kTargets = 3;
    ID3D11Texture2D* encode_target[kTargets] = {};
    ID3D11RenderTargetView* encode_rtv[kTargets] = {};
    int target_index = 0;

    std::thread encoder_thread;
    std::mutex queue_mutex;
    std::condition_variable queue_ready;
    std::atomic<bool> encoding{false};
    bool has_pending = false;
    int pending_target = 0;
    uint64_t pending_pts = 0;
    bool pending_idr = false;

    // The encode session is opened on the encoder thread itself (not here on the app's thread): NVENC
    // ties a DirectX session to the thread/device that created it, so mapping the input from a
    // different thread than the one that opened the session fails (nvEncMapInputResource INVALID_PARAM).
    EncoderConfig enc_cfg;
    std::atomic<int> enc_ready{0};   // 0 = still initialising, 1 = ready, -1 = init failed

    HeadPose pose;
    HandsState hands;
    uint64_t frame_index = 0;
    uint64_t encoded_index = 0;

    LARGE_INTEGER qpc_freq{}, qpc_base{};
    bool session_running = false;
    XrSessionState state = XR_SESSION_STATE_UNKNOWN;
    std::mutex event_mutex;
    std::deque<XrSessionState> pending_states;

    uint64_t next_action_handle = 0xA000;
};
Runtime g;

int64_t now_ns() {
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    return (c.QuadPart - g.qpc_base.QuadPart) * 1000000000LL / g.qpc_freq.QuadPart;
}

// Rotate v by quaternion q (x,y,z,w).
XrVector3f rotate(const XrQuaternionf& q, XrVector3f v) {
    const float tx = 2 * (q.y * v.z - q.z * v.y);
    const float ty = 2 * (q.z * v.x - q.x * v.z);
    const float tz = 2 * (q.x * v.y - q.y * v.x);
    return {v.x + q.w * tx + (q.y * tz - q.z * ty), v.y + q.w * ty + (q.z * tx - q.x * tz),
            v.z + q.w * tz + (q.x * ty - q.y * tx)};
}

#define LOGCALL() log(__func__)

// ---- M0 surface (unchanged) --------------------------------------------------------------------

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEnumerateInstanceExtensionProperties(
    const char*, uint32_t capacity, uint32_t* count, XrExtensionProperties* props) {
    static const char* kExts[] = {"XR_KHR_D3D11_enable", "XR_EXT_hand_tracking"};
    const uint32_t n = uint32_t(sizeof(kExts) / sizeof(kExts[0]));
    *count = n;
    if (capacity == 0) return XR_SUCCESS;
    if (capacity < n) return XR_ERROR_SIZE_INSUFFICIENT;
    for (uint32_t i = 0; i < n; ++i) {
        std::strncpy(props[i].extensionName, kExts[i], XR_MAX_EXTENSION_NAME_SIZE - 1);
        props[i].extensionVersion = 1;
    }
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEnumerateApiLayerProperties(uint32_t, uint32_t* count,
                                                                 XrApiLayerProperties*) {
    *count = 0;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrCreateInstance(const XrInstanceCreateInfo* info,
                                                     XrInstance* instance) {
    LOGCALL();
    if (info != nullptr)
        for (uint32_t i = 0; i < info->enabledExtensionCount; ++i)
            log(std::string("  ext: ") + info->enabledExtensionNames[i]);
    load_config();
    QueryPerformanceFrequency(&g.qpc_freq);
    QueryPerformanceCounter(&g.qpc_base);
    *instance = reinterpret_cast<XrInstance>(kInstanceHandle);
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrDestroyInstance(XrInstance) {
    LOGCALL();
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetInstanceProperties(XrInstance, XrInstanceProperties* p) {
    if (p) {
        p->runtimeVersion = XR_MAKE_VERSION(0, 1, 0);
        std::strncpy(p->runtimeName, "xrwired", XR_MAX_RUNTIME_NAME_SIZE - 1);
    }
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetSystem(XrInstance, const XrSystemGetInfo* info,
                                                XrSystemId* id) {
    if (info && info->formFactor != XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY)
        return XR_ERROR_FORM_FACTOR_UNSUPPORTED;
    *id = kSystemId;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetSystemProperties(XrInstance, XrSystemId,
                                                          XrSystemProperties* p) {
    if (p) {
        std::strncpy(p->systemName, "Galaxy XR (xrwired)", XR_MAX_SYSTEM_NAME_SIZE - 1);
        p->graphicsProperties.maxSwapchainImageWidth = g_cfg.eye_w * 2;
        p->graphicsProperties.maxSwapchainImageHeight = g_cfg.eye_h * 2;
        p->graphicsProperties.maxLayerCount = 16;
        p->trackingProperties.orientationTracking = XR_TRUE;
        p->trackingProperties.positionTracking = XR_TRUE;
        // hand tracking (M2) advertised via a chained XrSystemHandTrackingPropertiesEXT
        for (auto* n = static_cast<XrBaseOutStructure*>(p->next); n; n = n->next)
            if (n->type == XR_TYPE_SYSTEM_HAND_TRACKING_PROPERTIES_EXT)
                reinterpret_cast<XrSystemHandTrackingPropertiesEXT*>(n)->supportsHandTracking = XR_TRUE;
    }
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEnumerateViewConfigurations(
    XrInstance, XrSystemId, uint32_t cap, uint32_t* count, XrViewConfigurationType* types) {
    *count = 1;
    if (cap == 0) return XR_SUCCESS;
    types[0] = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetViewConfigurationProperties(
    XrInstance, XrSystemId, XrViewConfigurationType t, XrViewConfigurationProperties* p) {
    if (p) {
        p->viewConfigurationType = t;
        p->fovMutable = XR_TRUE;
    }
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEnumerateViewConfigurationViews(
    XrInstance, XrSystemId, XrViewConfigurationType, uint32_t cap, uint32_t* count,
    XrViewConfigurationView* views) {
    *count = 2;
    if (cap == 0) return XR_SUCCESS;
    if (cap < 2) return XR_ERROR_SIZE_INSUFFICIENT;
    for (uint32_t i = 0; i < 2; ++i) {
        views[i].recommendedImageRectWidth = views[i].maxImageRectWidth = g_cfg.eye_w;
        views[i].recommendedImageRectHeight = views[i].maxImageRectHeight = g_cfg.eye_h;
        views[i].recommendedSwapchainSampleCount = views[i].maxSwapchainSampleCount = 1;
    }
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEnumerateEnvironmentBlendModes(
    XrInstance, XrSystemId, XrViewConfigurationType, uint32_t cap, uint32_t* count,
    XrEnvironmentBlendMode* modes) {
    *count = 1;
    if (cap == 0) return XR_SUCCESS;
    modes[0] = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetD3D11GraphicsRequirementsKHR(
    XrInstance, XrSystemId, XrGraphicsRequirementsD3D11KHR* req) {
    LOGCALL();
    if (req) {
        req->adapterLuid = LUID{};
        req->minFeatureLevel = D3D_FEATURE_LEVEL_11_0;
    }
    return XR_SUCCESS;
}

// ---- session / swapchains / frame (M1) ---------------------------------------------------------

// Encoder thread: takes the frame xrEndFrame most recently blitted into the ring, encodes it with
// NVENC and streams it. Kept off the app's frame loop so encode time never caps the render rate.
void encode_loop() {
    // Open the NVENC session here, on this thread, so every later map/encode/unmap runs on the same
    // thread that created it (see enc_ready in Runtime).
    if (!g.encoder.init(g.enc_device, g.enc_cfg)) {
        log("  encoder init failed: " + g.encoder.error());
        g.enc_ready.store(-1);
        return;
    }
    g.enc_ready.store(1);

    std::vector<uint8_t> frame;
    while (g.encoding) {
        int index = -1;
        uint64_t pts = 0;
        bool idr = false;
        {
            std::unique_lock<std::mutex> lock(g.queue_mutex);
            g.queue_ready.wait(lock, [] { return g.has_pending || !g.encoding; });
            if (!g.encoding) break;
            index = g.pending_target;
            pts = g.pending_pts;
            idr = g.pending_idr;
            g.has_pending = false;
        }
        frame.clear();
        if (!g.encoder.encode(g.encode_target[index], pts, idr, &frame)) {
            log("encode failed: " + g.encoder.error());
            continue;
        }
        g.stream.send_frame(frame.data(), frame.size(), pts);
        if (++g.encoded_index % 72 == 0) {
            const StreamStats s = g.stream.stats();
            log("frames=" + std::to_string(g.frame_index) + " encoded=" +
                std::to_string(g.encoded_index) + " encode=" +
                std::to_string(g.encoder.last_encode_ms()) + "ms sent=" +
                std::to_string(s.frames_sent) + " decoded_ms=" +
                std::to_string(s.last_send_to_decoded_ms) + " photons_ms=" +
                std::to_string(s.last_send_to_photons_ms));
        }
    }
}

void start_encoder_thread() {
    g.encoding = true;
    g.encoder_thread = std::thread(encode_loop);
}

void stop_encoder_thread() {
    g.encoding = false;
    g.queue_ready.notify_all();
    if (g.encoder_thread.joinable()) g.encoder_thread.join();
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrCreateSession(XrInstance, const XrSessionCreateInfo* info,
                                                    XrSession* session) {
    LOGCALL();
    const XrGraphicsBindingD3D11KHR* d3d = nullptr;
    for (auto* n = static_cast<const XrBaseInStructure*>(info->next); n; n = n->next)
        if (n->type == XR_TYPE_GRAPHICS_BINDING_D3D11_KHR)
            d3d = reinterpret_cast<const XrGraphicsBindingD3D11KHR*>(n);
    if (d3d == nullptr || d3d->device == nullptr) {
        log("  no D3D11 graphics binding");
        return XR_ERROR_GRAPHICS_DEVICE_INVALID;
    }
    g.device = d3d->device;
    g.device->AddRef();
    g.device->GetImmediateContext(&g.context);

    // Our own device for NVENC (see Runtime::enc_device).
    const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0};
    if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, levels, 2,
                                 D3D11_SDK_VERSION, &g.enc_device, nullptr, &g.enc_context))) {
        log("  encoder D3D11 device creation failed");
        return XR_ERROR_RUNTIME_FAILURE;
    }

    const uint32_t sbs_w = g_cfg.eye_w * 2, sbs_h = g_cfg.eye_h;
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = sbs_w;
    td.Height = sbs_h;
    td.MipLevels = td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    td.MiscFlags = D3D11_RESOURCE_MISC_SHARED;   // so the encoder device can open and read them
    // Ring targets live on the APP device (the render thread blits into them); the encoder opens them
    // shared on its own device and copies out. The 3-deep ring means the render thread is two frames
    // ahead of whichever target the encoder is reading, so a blit never overwrites an in-flight copy.
    for (int i = 0; i < Runtime::kTargets; ++i) {
        if (FAILED(g.device->CreateTexture2D(&td, nullptr, &g.encode_target[i])) ||
            FAILED(g.device->CreateRenderTargetView(g.encode_target[i], nullptr, &g.encode_rtv[i]))) {
            log("  encode target creation failed");
            return XR_ERROR_RUNTIME_FAILURE;
        }
    }
    if (!g.blitter.init(g.device)) return XR_ERROR_RUNTIME_FAILURE;

    // The encoder is initialised on its own thread (see encode_loop); we only prepare its config here.
    EncoderConfig& ec = g.enc_cfg;
    ec.width = int(sbs_w);
    ec.height = int(sbs_h);
    ec.fps = g_cfg.fps;
    ec.mbps = g_cfg.mbps;
    ec.hevc = g_cfg.codec.rfind("hevc", 0) == 0;
    ec.ten_bit = g_cfg.codec == "hevc10";
    const int codec_id = ec.hevc ? (ec.ten_bit ? 4 : 3) : 0;
    g.stream.on_pose([](uint64_t id, const float v[21]) {
        std::lock_guard<std::mutex> lock(g.pose.mutex);
        g.pose.orientation = {v[0], v[1], v[2], v[3]};   // x,y,z,w
        g.pose.position = {v[4], v[5], v[6]};
        std::memcpy(g.pose.fov, v + 7, sizeof(g.pose.fov));
        g.pose.have_fov = v[8] != 0.0f;                  // right-angle of left eye is nonzero once real
        g.pose.id.store(id);
    });
    g.stream.on_hands([](uint64_t, bool left, bool right, const float j[416]) {
        std::lock_guard<std::mutex> lock(g.hands.mutex);
        g.hands.active[0] = left;
        g.hands.active[1] = right;
        std::memcpy(g.hands.joint[0], j, 26 * 8 * sizeof(float));
        std::memcpy(g.hands.joint[1], j + 26 * 8, 26 * 8 * sizeof(float));
    });
    g.stream.connect_to(g_cfg.headset_ip, g_cfg.port, int(sbs_w), int(sbs_h), g_cfg.fps, codec_id);
    start_encoder_thread();
    while (g.enc_ready.load() == 0) Sleep(1);   // let the worker open the NVENC session (or report failure)
    if (g.enc_ready.load() < 0) {
        stop_encoder_thread();
        return XR_ERROR_RUNTIME_FAILURE;
    }

    g.state = XR_SESSION_STATE_IDLE;
    {
        std::lock_guard<std::mutex> lock(g.event_mutex);
        g.pending_states.push_back(XR_SESSION_STATE_IDLE);
        g.pending_states.push_back(XR_SESSION_STATE_READY);
    }
    *session = reinterpret_cast<XrSession>(0x7001);
    log("  session up, streaming " + std::to_string(sbs_w) + "x" + std::to_string(sbs_h) + " to " +
        g_cfg.headset_ip);
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrDestroySession(XrSession) {
    LOGCALL();
    stop_encoder_thread();
    g.stream.stop();
    g.encoder.shutdown();
    g.blitter.release();
    for (int i = 0; i < Runtime::kTargets; ++i) {
        if (g.encode_rtv[i]) g.encode_rtv[i]->Release();
        if (g.encode_target[i]) g.encode_target[i]->Release();
        g.encode_rtv[i] = nullptr;
        g.encode_target[i] = nullptr;
    }
    if (g.enc_context) g.enc_context->Release();
    if (g.enc_device) g.enc_device->Release();
    if (g.context) g.context->Release();
    if (g.device) g.device->Release();
    g.enc_context = nullptr;
    g.enc_device = nullptr;
    g.context = nullptr;
    g.device = nullptr;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrBeginSession(XrSession, const XrSessionBeginInfo*) {
    LOGCALL();
    g.session_running = true;
    std::lock_guard<std::mutex> lock(g.event_mutex);
    g.pending_states.push_back(XR_SESSION_STATE_SYNCHRONIZED);
    g.pending_states.push_back(XR_SESSION_STATE_VISIBLE);
    g.pending_states.push_back(XR_SESSION_STATE_FOCUSED);
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEndSession(XrSession) {
    LOGCALL();
    g.session_running = false;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrRequestExitSession(XrSession) {
    std::lock_guard<std::mutex> lock(g.event_mutex);
    g.pending_states.push_back(XR_SESSION_STATE_STOPPING);
    g.pending_states.push_back(XR_SESSION_STATE_EXITING);
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrPollEvent(XrInstance, XrEventDataBuffer* ev) {
    std::lock_guard<std::mutex> lock(g.event_mutex);
    if (g.pending_states.empty()) return XR_EVENT_UNAVAILABLE;
    g.state = g.pending_states.front();
    g.pending_states.pop_front();
    auto* e = reinterpret_cast<XrEventDataSessionStateChanged*>(ev);
    e->type = XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED;
    e->next = nullptr;
    e->session = reinterpret_cast<XrSession>(0x7001);
    e->state = g.state;
    e->time = now_ns();
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEnumerateReferenceSpaces(XrSession, uint32_t cap,
                                                               uint32_t* count,
                                                               XrReferenceSpaceType* spaces) {
    *count = 2;
    if (cap == 0) return XR_SUCCESS;
    if (cap < 2) return XR_ERROR_SIZE_INSUFFICIENT;
    spaces[0] = XR_REFERENCE_SPACE_TYPE_LOCAL;
    spaces[1] = XR_REFERENCE_SPACE_TYPE_VIEW;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrCreateReferenceSpace(XrSession,
                                                           const XrReferenceSpaceCreateInfo* info,
                                                           XrSpace* space) {
    auto* s = new Space();
    s->type = info->referenceSpaceType;
    *space = reinterpret_cast<XrSpace>(s);
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrDestroySpace(XrSpace space) {
    delete reinterpret_cast<Space*>(space);
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrLocateSpace(XrSpace space, XrSpace, XrTime,
                                                  XrSpaceLocation* loc) {
    auto* s = reinterpret_cast<Space*>(space);
    loc->locationFlags = 0;
    loc->pose = {{0, 0, 0, 1}, {0, 0, 0}};
    if (s && s->type == XR_REFERENCE_SPACE_TYPE_VIEW && !s->is_action) {
        std::lock_guard<std::mutex> lock(g.pose.mutex);
        loc->pose.orientation = g.pose.orientation;
        loc->pose.position = g.pose.position;
        loc->locationFlags = XR_SPACE_LOCATION_ORIENTATION_VALID_BIT | XR_SPACE_LOCATION_POSITION_VALID_BIT |
                             XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT | XR_SPACE_LOCATION_POSITION_TRACKED_BIT;
    }
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEnumerateSwapchainFormats(XrSession, uint32_t cap,
                                                                uint32_t* count, int64_t* formats) {
    static const int64_t kFormats[] = {DXGI_FORMAT_R8G8B8A8_UNORM_SRGB, DXGI_FORMAT_R8G8B8A8_UNORM,
                                       DXGI_FORMAT_B8G8R8A8_UNORM_SRGB, DXGI_FORMAT_B8G8R8A8_UNORM};
    const uint32_t n = uint32_t(sizeof(kFormats) / sizeof(kFormats[0]));
    *count = n;
    if (cap == 0) return XR_SUCCESS;
    if (cap < n) return XR_ERROR_SIZE_INSUFFICIENT;
    std::memcpy(formats, kFormats, sizeof(kFormats));
    return XR_SUCCESS;
}

DXGI_FORMAT to_typeless(int64_t format) {
    switch (format) {
        case DXGI_FORMAT_R8G8B8A8_UNORM:
        case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
        case DXGI_FORMAT_R8G8B8A8_TYPELESS:
            return DXGI_FORMAT_R8G8B8A8_TYPELESS;   // app makes a typed sRGB RTV, we make a raw UNORM SRV
        case DXGI_FORMAT_B8G8R8A8_UNORM:
        case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
        case DXGI_FORMAT_B8G8R8A8_TYPELESS:
            return DXGI_FORMAT_B8G8R8A8_TYPELESS;
        default:
            return static_cast<DXGI_FORMAT>(format);
    }
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrCreateSwapchain(XrSession, const XrSwapchainCreateInfo* info,
                                                      XrSwapchain* out) {
    auto* sc = new Swapchain();
    sc->width = info->width;
    sc->height = info->height;
    const uint32_t kImages = 3;
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = info->width;
    td.Height = info->height;
    td.MipLevels = 1;
    td.ArraySize = info->arraySize ? info->arraySize : 1;
    td.Format = to_typeless(info->format);
    td.SampleDesc.Count = info->sampleCount ? info->sampleCount : 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    for (uint32_t i = 0; i < kImages; ++i) {
        ID3D11Texture2D* tex = nullptr;
        if (FAILED(g.device->CreateTexture2D(&td, nullptr, &tex))) {
            log("xrCreateSwapchain: CreateTexture2D failed");
            delete sc;
            return XR_ERROR_RUNTIME_FAILURE;
        }
        sc->images.push_back(tex);
    }
    *out = reinterpret_cast<XrSwapchain>(sc);
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrDestroySwapchain(XrSwapchain swapchain) {
    auto* sc = reinterpret_cast<Swapchain*>(swapchain);
    for (auto* t : sc->images) t->Release();
    delete sc;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEnumerateSwapchainImages(XrSwapchain swapchain, uint32_t cap,
                                                               uint32_t* count,
                                                               XrSwapchainImageBaseHeader* images) {
    auto* sc = reinterpret_cast<Swapchain*>(swapchain);
    *count = uint32_t(sc->images.size());
    if (cap == 0) return XR_SUCCESS;
    if (cap < sc->images.size()) return XR_ERROR_SIZE_INSUFFICIENT;
    auto* d3d = reinterpret_cast<XrSwapchainImageD3D11KHR*>(images);
    for (size_t i = 0; i < sc->images.size(); ++i) d3d[i].texture = sc->images[i];
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrAcquireSwapchainImage(XrSwapchain swapchain,
                                                            const XrSwapchainImageAcquireInfo*,
                                                            uint32_t* index) {
    auto* sc = reinterpret_cast<Swapchain*>(swapchain);
    sc->acquired = (sc->acquired + 1) % uint32_t(sc->images.size());
    *index = sc->acquired;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrWaitSwapchainImage(XrSwapchain, const XrSwapchainImageWaitInfo*) {
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrReleaseSwapchainImage(XrSwapchain swapchain,
                                                            const XrSwapchainImageReleaseInfo*) {
    auto* sc = reinterpret_cast<Swapchain*>(swapchain);
    sc->released = sc->acquired;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrWaitFrame(XrSession, const XrFrameWaitInfo*,
                                                XrFrameState* state) {
    const int64_t period = 1000000000LL / g_cfg.fps;
    static int64_t next = 0;
    const int64_t now = now_ns();
    if (next == 0) next = now;
    if (next > now) {
        Sleep(DWORD((next - now) / 1000000LL));
    }
    next += period;
    if (next < now) next = now + period;
    state->type = XR_TYPE_FRAME_STATE;
    state->predictedDisplayTime = now_ns() + period;
    state->predictedDisplayPeriod = period;
    state->shouldRender = g.session_running ? XR_TRUE : XR_FALSE;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrBeginFrame(XrSession, const XrFrameBeginInfo*) {
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrLocateViews(XrSession, const XrViewLocateInfo* info,
                                                  XrViewState* vstate, uint32_t cap,
                                                  uint32_t* count, XrView* views) {
    *count = 2;
    vstate->viewStateFlags = XR_VIEW_STATE_ORIENTATION_VALID_BIT | XR_VIEW_STATE_POSITION_VALID_BIT |
                             XR_VIEW_STATE_ORIENTATION_TRACKED_BIT | XR_VIEW_STATE_POSITION_TRACKED_BIT;
    if (cap == 0) return XR_SUCCESS;
    if (cap < 2) return XR_ERROR_SIZE_INSUFFICIENT;
    (void)info;
    std::lock_guard<std::mutex> lock(g.pose.mutex);
    for (uint32_t eye = 0; eye < 2; ++eye) {
        const float sign = eye == 0 ? -1.0f : 1.0f;
        XrVector3f offset = rotate(g.pose.orientation, {sign * g_cfg.ipd * 0.5f, 0, 0});
        views[eye].type = XR_TYPE_VIEW;
        views[eye].pose.orientation = g.pose.orientation;
        views[eye].pose.position = {g.pose.position.x + offset.x, g.pose.position.y + offset.y,
                                    g.pose.position.z + offset.z};
        const float* f = g.pose.fov + eye * 4;
        views[eye].fov = {f[0], f[1], f[2], f[3]};   // angleLeft, angleRight, angleUp, angleDown
    }
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrEndFrame(XrSession, const XrFrameEndInfo* info) {
    if (info == nullptr || info->layerCount == 0 || g.encode_target[0] == nullptr) return XR_SUCCESS;
    const XrCompositionLayerProjection* proj = nullptr;
    for (uint32_t i = 0; i < info->layerCount; ++i)
        if (info->layers[i]->type == XR_TYPE_COMPOSITION_LAYER_PROJECTION)
            proj = reinterpret_cast<const XrCompositionLayerProjection*>(info->layers[i]);
    if (proj == nullptr || proj->viewCount < 2) return XR_SUCCESS;

    // Backpressure: if the headset has not acked several recent frames it is falling behind, so drop
    // this one instead of growing a decode/display queue (which would balloon latency). Checked before
    // the blit so a falling-behind headset costs us nothing on the frame loop.
    const StreamStats st = g.stream.stats();
    if (st.frames_sent > st.acks_decoded + 3) {
        g.frame_index++;
        return XR_SUCCESS;
    }

    // Blit both eyes into the next ring target on the app's immediate context, then hand its index to
    // the encoder thread. The ring is deep enough that the encoder reads a target we won't touch again
    // for two more frames, so the blit and the encode never race on the same texture.
    const int index = g.target_index;
    for (uint32_t eye = 0; eye < 2; ++eye) {
        const XrCompositionLayerProjectionView& v = proj->views[eye];
        auto* sc = reinterpret_cast<Swapchain*>(v.subImage.swapchain);
        if (sc == nullptr || sc->images.empty()) continue;
        ID3D11Texture2D* tex = sc->images[sc->released];
        const float u0 = float(v.subImage.imageRect.offset.x) / sc->width;
        const float v0 = float(v.subImage.imageRect.offset.y) / sc->height;
        const float uW = float(v.subImage.imageRect.extent.width) / sc->width;
        const float vH = float(v.subImage.imageRect.extent.height) / sc->height;
        g.blitter.blit(g.device, g.context, tex, u0, v0, uW, vH, g.encode_rtv[index],
                       eye == 0 ? 0.0f : float(g_cfg.eye_w), 0.0f, float(g_cfg.eye_w), float(g_cfg.eye_h));
    }
    g.context->Flush();   // make the blit visible to the encoder thread's NVENC read

    const uint64_t pose_id = g.pose.id.load();
    const uint64_t pts = pose_id != 0 ? pose_id : g.frame_index + 1;
    const bool idr = g.stream.take_keyframe_request() || g.frame_index == 0;
    {
        std::lock_guard<std::mutex> lock(g.queue_mutex);
        g.pending_target = index;
        g.pending_pts = pts;
        g.pending_idr = idr;
        g.has_pending = true;
    }
    g.queue_ready.notify_one();
    g.target_index = (g.target_index + 1) % Runtime::kTargets;
    g.frame_index++;
    return XR_SUCCESS;
}

// ---- hand tracking (XR_EXT_hand_tracking): joints forwarded from the headset ------------------

XRAPI_ATTR XrResult XRAPI_CALL impl_xrCreateHandTrackerEXT(XrSession,
                                                           const XrHandTrackerCreateInfoEXT* info,
                                                           XrHandTrackerEXT* tracker) {
    const uintptr_t hand = info && info->hand == XR_HAND_RIGHT_EXT ? 2 : 1;   // 1 = left, 2 = right
    *tracker = reinterpret_cast<XrHandTrackerEXT>(hand);
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL impl_xrDestroyHandTrackerEXT(XrHandTrackerEXT) { return XR_SUCCESS; }

XRAPI_ATTR XrResult XRAPI_CALL impl_xrLocateHandJointsEXT(XrHandTrackerEXT tracker,
                                                          const XrHandJointsLocateInfoEXT*,
                                                          XrHandJointLocationsEXT* locations) {
    const int hand = reinterpret_cast<uintptr_t>(tracker) == 2 ? 1 : 0;
    if (locations == nullptr || locations->jointLocations == nullptr) return XR_ERROR_VALIDATION_FAILURE;
    const uint32_t n = locations->jointCount < 26 ? locations->jointCount : 26;
    std::lock_guard<std::mutex> lock(g.hands.mutex);
    locations->isActive = g.hands.active[hand] ? XR_TRUE : XR_FALSE;
    const XrSpaceLocationFlags flags = g.hands.active[hand]
        ? (XR_SPACE_LOCATION_ORIENTATION_VALID_BIT | XR_SPACE_LOCATION_POSITION_VALID_BIT |
           XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT | XR_SPACE_LOCATION_POSITION_TRACKED_BIT)
        : 0;
    for (uint32_t j = 0; j < n; ++j) {
        const float* d = g.hands.joint[hand] + j * 8;
        XrHandJointLocationEXT& out = locations->jointLocations[j];
        out.locationFlags = flags;
        out.pose.position = {d[0], d[1], d[2]};
        out.pose.orientation = {d[3], d[4], d[5], d[6]};
        out.radius = d[7];
    }
    return XR_SUCCESS;
}

// ---- action system stubs (enough for apps to init; head-look for now) ---------------------------

XRAPI_ATTR XrResult XRAPI_CALL impl_xrCreateActionSet(XrInstance, const XrActionSetCreateInfo*,
                                                      XrActionSet* set) {
    *set = reinterpret_cast<XrActionSet>(++g.next_action_handle);
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrDestroyActionSet(XrActionSet) { return XR_SUCCESS; }
XRAPI_ATTR XrResult XRAPI_CALL impl_xrCreateAction(XrActionSet, const XrActionCreateInfo*,
                                                   XrAction* action) {
    *action = reinterpret_cast<XrAction>(++g.next_action_handle);
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrDestroyAction(XrAction) { return XR_SUCCESS; }
XRAPI_ATTR XrResult XRAPI_CALL impl_xrSuggestInteractionProfileBindings(
    XrInstance, const XrInteractionProfileSuggestedBinding*) {
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrAttachSessionActionSets(XrSession,
                                                              const XrSessionActionSetsAttachInfo*) {
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrSyncActions(XrSession, const XrActionsSyncInfo*) {
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetActionStateBoolean(XrSession, const XrActionStateGetInfo*,
                                                            XrActionStateBoolean* s) {
    *s = {XR_TYPE_ACTION_STATE_BOOLEAN};
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetActionStateFloat(XrSession, const XrActionStateGetInfo*,
                                                          XrActionStateFloat* s) {
    *s = {XR_TYPE_ACTION_STATE_FLOAT};
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetActionStateVector2f(XrSession, const XrActionStateGetInfo*,
                                                             XrActionStateVector2f* s) {
    *s = {XR_TYPE_ACTION_STATE_VECTOR2F};
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetActionStatePose(XrSession, const XrActionStateGetInfo*,
                                                         XrActionStatePose* s) {
    *s = {XR_TYPE_ACTION_STATE_POSE};
    s->isActive = XR_FALSE;
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrCreateActionSpace(XrSession, const XrActionSpaceCreateInfo*,
                                                        XrSpace* space) {
    auto* s = new Space();
    s->is_action = true;
    *space = reinterpret_cast<XrSpace>(s);
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrGetCurrentInteractionProfile(
    XrSession, XrPath, XrInteractionProfileState* p) {
    if (p) p->interactionProfile = XR_NULL_PATH;
    return XR_SUCCESS;
}

// ---- path/string helpers -----------------------------------------------------------------------

std::string g_paths[128];
uint32_t g_path_count = 0;

XRAPI_ATTR XrResult XRAPI_CALL impl_xrStringToPath(XrInstance, const char* str, XrPath* path) {
    for (uint32_t i = 0; i < g_path_count; ++i)
        if (g_paths[i] == str) { *path = i + 1; return XR_SUCCESS; }
    if (g_path_count >= 128) return XR_ERROR_PATH_COUNT_EXCEEDED;
    g_paths[g_path_count] = str;
    *path = ++g_path_count;
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrPathToString(XrInstance, XrPath path, uint32_t cap,
                                                   uint32_t* count, char* buf) {
    if (path == 0 || path > g_path_count) return XR_ERROR_PATH_INVALID;
    const std::string& s = g_paths[path - 1];
    *count = uint32_t(s.size() + 1);
    if (cap == 0) return XR_SUCCESS;
    if (cap < *count) return XR_ERROR_SIZE_INSUFFICIENT;
    std::memcpy(buf, s.c_str(), *count);
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrResultToString(XrInstance, XrResult v, char* buf) {
    std::snprintf(buf, XR_MAX_RESULT_STRING_SIZE, "XR_%d", int(v));
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL impl_xrStructureTypeToString(XrInstance, XrStructureType v, char* buf) {
    std::snprintf(buf, XR_MAX_STRUCTURE_NAME_SIZE, "XR_TYPE_%d", int(v));
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL runtime_xrGetInstanceProcAddr(XrInstance, const char* name,
                                                             PFN_xrVoidFunction* function) {
#define BIND(fn)                                                     \
    if (std::strcmp(name, #fn) == 0) {                              \
        *function = reinterpret_cast<PFN_xrVoidFunction>(impl_##fn); \
        return XR_SUCCESS;                                          \
    }
    BIND(xrEnumerateInstanceExtensionProperties);
    BIND(xrEnumerateApiLayerProperties);
    BIND(xrCreateInstance);
    BIND(xrDestroyInstance);
    BIND(xrGetInstanceProperties);
    BIND(xrGetSystem);
    BIND(xrGetSystemProperties);
    BIND(xrEnumerateViewConfigurations);
    BIND(xrGetViewConfigurationProperties);
    BIND(xrEnumerateViewConfigurationViews);
    BIND(xrEnumerateEnvironmentBlendModes);
    BIND(xrGetD3D11GraphicsRequirementsKHR);
    BIND(xrCreateSession);
    BIND(xrDestroySession);
    BIND(xrBeginSession);
    BIND(xrEndSession);
    BIND(xrRequestExitSession);
    BIND(xrPollEvent);
    BIND(xrEnumerateReferenceSpaces);
    BIND(xrCreateReferenceSpace);
    BIND(xrDestroySpace);
    BIND(xrLocateSpace);
    BIND(xrEnumerateSwapchainFormats);
    BIND(xrCreateSwapchain);
    BIND(xrDestroySwapchain);
    BIND(xrEnumerateSwapchainImages);
    BIND(xrAcquireSwapchainImage);
    BIND(xrWaitSwapchainImage);
    BIND(xrReleaseSwapchainImage);
    BIND(xrWaitFrame);
    BIND(xrBeginFrame);
    BIND(xrLocateViews);
    BIND(xrEndFrame);
    BIND(xrCreateActionSet);
    BIND(xrDestroyActionSet);
    BIND(xrCreateAction);
    BIND(xrDestroyAction);
    BIND(xrSuggestInteractionProfileBindings);
    BIND(xrAttachSessionActionSets);
    BIND(xrSyncActions);
    BIND(xrGetActionStateBoolean);
    BIND(xrGetActionStateFloat);
    BIND(xrGetActionStateVector2f);
    BIND(xrGetActionStatePose);
    BIND(xrCreateActionSpace);
    BIND(xrGetCurrentInteractionProfile);
    BIND(xrCreateHandTrackerEXT);
    BIND(xrDestroyHandTrackerEXT);
    BIND(xrLocateHandJointsEXT);
    BIND(xrStringToPath);
    BIND(xrPathToString);
    BIND(xrResultToString);
    BIND(xrStructureTypeToString);
#undef BIND
    log(std::string("gipa MISS: ") + name);
    *function = nullptr;
    return XR_ERROR_FUNCTION_UNSUPPORTED;
}

}  // namespace

extern "C" __declspec(dllexport) XRAPI_ATTR XrResult XRAPI_CALL xrNegotiateLoaderRuntimeInterface(
    const XrNegotiateLoaderInfo* loaderInfo, XrNegotiateRuntimeRequest* runtimeRequest) {
    log("xrNegotiateLoaderRuntimeInterface");
    if (loaderInfo == nullptr || runtimeRequest == nullptr) return XR_ERROR_INITIALIZATION_FAILED;
    if (loaderInfo->structType != XR_LOADER_INTERFACE_STRUCT_LOADER_INFO ||
        loaderInfo->structVersion != XR_LOADER_INFO_STRUCT_VERSION ||
        loaderInfo->structSize != sizeof(XrNegotiateLoaderInfo))
        return XR_ERROR_INITIALIZATION_FAILED;
    if (runtimeRequest->structType != XR_LOADER_INTERFACE_STRUCT_RUNTIME_REQUEST ||
        runtimeRequest->structVersion != XR_RUNTIME_INFO_STRUCT_VERSION ||
        runtimeRequest->structSize != sizeof(XrNegotiateRuntimeRequest))
        return XR_ERROR_INITIALIZATION_FAILED;
    if (XR_CURRENT_LOADER_RUNTIME_VERSION < loaderInfo->minInterfaceVersion ||
        XR_CURRENT_LOADER_RUNTIME_VERSION > loaderInfo->maxInterfaceVersion)
        return XR_ERROR_INITIALIZATION_FAILED;
    runtimeRequest->runtimeInterfaceVersion = XR_CURRENT_LOADER_RUNTIME_VERSION;
    runtimeRequest->runtimeApiVersion = XR_CURRENT_API_VERSION;
    runtimeRequest->getInstanceProcAddr = runtime_xrGetInstanceProcAddr;
    log("  negotiated OK");
    return XR_SUCCESS;
}
