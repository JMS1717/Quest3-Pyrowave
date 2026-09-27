// xrw_hands - a full D3D11 OpenXR app that drives OUR runtime end to end and visualizes hand tracking.
//
// It is the hand-tracking sibling of xrw_probe.cpp: same runtime-direct harness (negotiate the runtime
// interface with no official loader, create a session against a real ID3D11Device, run a wait/begin/
// locate/acquire/release/end frame loop). On top of that it enables XR_EXT_hand_tracking, creates a
// left and right hand tracker, locates the 26 joints of each hand per frame in LOCAL space, and draws a
// small colored square at every valid joint's projected screen position (left = green, right = cyan).
//
// Projection is hand-rolled from XrView.pose (view matrix = inverse rigid transform) and XrView.fov
// (asymmetric OpenXR projection, D3D z 0..1), computed on the CPU straight to NDC; the shaders are a
// pass-through VS over an NDC vertex buffer and a PS that emits a per-draw constant color.
//
// Success bar for now is a clean compile with the correct call sequence; it is meant to be worn later.
#define _CRT_SECURE_NO_WARNINGS   // strncpy into the fixed-size OpenXR name fields is intentional
#include <d3d11.h>          // must precede openxr_platform.h so ID3D11Device/Texture2D are defined
#include <d3d11_1.h>        // ID3D11DeviceContext1::ClearView, kept for parity with the probe
#include <d3dcompiler.h>    // D3DCompile for the pass-through quad shaders
#include <windows.h>

#define XR_USE_GRAPHICS_API_D3D11
#include <openxr/openxr.h>
#include <openxr/openxr_loader_negotiation.h>
#include <openxr/openxr_platform.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3dcompiler.lib")

// On any XrResult failure: print "FAIL: <call> = <int>" and abort the process with code 1.
#define XRCHECK(expr)                                                            \
    do {                                                                         \
        XrResult _r = (expr);                                                    \
        if (XR_FAILED(_r)) {                                                     \
            std::printf("FAIL: %s = %d\n", #expr, int(_r));                      \
            std::exit(1);                                                        \
        }                                                                        \
    } while (0)

#define REQUIRE(cond, what)                                                     \
    do {                                                                         \
        if (!(cond)) {                                                          \
            std::printf("FAIL: %s = %d\n", what, -1);                            \
            std::exit(1);                                                        \
        }                                                                        \
    } while (0)

namespace {

PFN_xrGetInstanceProcAddr g_gipa = nullptr;
XrInstance g_instance = XR_NULL_HANDLE;

// Fetch any OpenXR entry point through the runtime's gipa and cast it to its PFN type. Instance-level
// functions need g_instance; the few that are valid with XR_NULL_HANDLE (enumerate extensions,
// create instance) are queried before we have one.
template <class F>
F get(const char* name) {
    PFN_xrVoidFunction fn = nullptr;
    if (XR_FAILED(g_gipa(g_instance, name, &fn)) || fn == nullptr) {
        std::printf("FAIL: gipa(%s) = %d\n", name, -1);
        std::exit(1);
    }
    return reinterpret_cast<F>(fn);
}

// One eye's swapchain plus a render target view per image, so we can render into whichever image the
// runtime hands back each frame.
struct EyeSwapchain {
    XrSwapchain handle = XR_NULL_HANDLE;
    uint32_t width = 0, height = 0;
    std::vector<ID3D11Texture2D*> textures;
    std::vector<ID3D11RenderTargetView*> rtvs;
};

// Project a world-space (LOCAL) point into an eye's NDC. Returns false if the point is behind the eye
// or falls outside the [-1,1] NDC box. View matrix is the inverse of the eye's rigid transform:
// camera-space c = R^T * (p - eyePos), with R the rotation of view.pose.orientation. The asymmetric
// projection comes straight from view.fov (D3D-style, z 0..1), but only x/y are needed for screen pos.
bool projectJoint(const XrView& view, const XrVector3f& p, float& ndcX, float& ndcY) {
    const XrQuaternionf& q = view.pose.orientation;
    const float x = q.x, y = q.y, z = q.z, w = q.w;
    // Rotation matrix R (row-major entries) from the eye orientation quaternion.
    const float r00 = 1.f - 2.f * (y * y + z * z);
    const float r01 = 2.f * (x * y - w * z);
    const float r02 = 2.f * (x * z + w * y);
    const float r10 = 2.f * (x * y + w * z);
    const float r11 = 1.f - 2.f * (x * x + z * z);
    const float r12 = 2.f * (y * z - w * x);
    const float r20 = 2.f * (x * z - w * y);
    const float r21 = 2.f * (y * z + w * x);
    const float r22 = 1.f - 2.f * (x * x + y * y);

    const float dx = p.x - view.pose.position.x;
    const float dy = p.y - view.pose.position.y;
    const float dz = p.z - view.pose.position.z;

    // camera-space = R^T * d  (transpose of R; rows of R^T are columns of R)
    const float cx = r00 * dx + r10 * dy + r20 * dz;
    const float cy = r01 * dx + r11 * dy + r21 * dz;
    const float cz = r02 * dx + r12 * dy + r22 * dz;

    // Asymmetric OpenXR projection from the fov half-angle tangents.
    const float tanL = std::tan(view.fov.angleLeft);
    const float tanR = std::tan(view.fov.angleRight);
    const float tanU = std::tan(view.fov.angleUp);
    const float tanD = std::tan(view.fov.angleDown);
    const float tanW = tanR - tanL;
    const float tanH = tanU - tanD;
    if (tanW == 0.f || tanH == 0.f) return false;

    // clip = P * (cx,cy,cz,1); the eye looks down -Z so clip.w = -cz.
    const float clipX = (2.f / tanW) * cx + ((tanR + tanL) / tanW) * cz;
    const float clipY = (2.f / tanH) * cy + ((tanU + tanD) / tanH) * cz;
    const float clipW = -cz;
    if (clipW <= 0.f) return false;   // behind the eye

    ndcX = clipX / clipW;
    ndcY = clipY / clipW;
    if (ndcX < -1.f || ndcX > 1.f || ndcY < -1.f || ndcY > 1.f) return false;
    return true;
}

// Compile one HLSL entry point to bytecode or abort with the compiler error text.
ID3DBlob* compileShader(const char* src, const char* entry, const char* target) {
    ID3DBlob* code = nullptr;
    ID3DBlob* err = nullptr;
    HRESULT hr = D3DCompile(src, std::strlen(src), nullptr, nullptr, nullptr, entry, target,
                            D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &code, &err);
    if (FAILED(hr)) {
        std::printf("FAIL: D3DCompile(%s) = 0x%lx: %s\n", entry, (unsigned long)hr,
                    err ? reinterpret_cast<const char*>(err->GetBufferPointer()) : "");
        std::exit(1);
    }
    if (err) err->Release();
    return code;
}

// Pass-through VS over an NDC vertex buffer + PS that emits a per-draw constant color.
const char* kQuadShaderHLSL =
    "struct VSIn  { float2 pos : POSITION; };\n"
    "struct VSOut { float4 pos : SV_POSITION; };\n"
    "VSOut VSMain(VSIn i) { VSOut o; o.pos = float4(i.pos, 0.0, 1.0); return o; }\n"
    "cbuffer ColorCB : register(b0) { float4 gColor; };\n"
    "float4 PSMain() : SV_TARGET { return gColor; }\n";

}  // namespace

int main(int argc, char** argv) {
    const char* dllPath = "xrwired_runtime.dll";
    int maxFrames = 600;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--frames") == 0 && i + 1 < argc) {
            maxFrames = std::atoi(argv[++i]);
        } else if (argv[i][0] != '-') {
            dllPath = argv[i];   // first positional arg is the runtime dll path
        }
    }

    // --- 1. D3D11 device + context (hardware, feature level 11_0+, single-threaded is fine). --------
    ID3D11Device* d3dDevice = nullptr;
    ID3D11DeviceContext* d3dContext = nullptr;
    const D3D_FEATURE_LEVEL wantLevels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0};
    D3D_FEATURE_LEVEL gotLevel = D3D_FEATURE_LEVEL_11_0;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
                                   D3D11_CREATE_DEVICE_SINGLETHREADED, wantLevels, 2, D3D11_SDK_VERSION,
                                   &d3dDevice, &gotLevel, &d3dContext);
    REQUIRE(SUCCEEDED(hr) && d3dDevice != nullptr, "D3D11CreateDevice");
    std::printf("d3d11 device up, feature level 0x%x\n", unsigned(gotLevel));

    // --- Negotiate the runtime interface directly (no official loader), like minixr_test.cpp. -------
    HMODULE lib = LoadLibraryA(dllPath);
    REQUIRE(lib != nullptr, "LoadLibrary(runtime dll)");
    auto negotiate = reinterpret_cast<PFN_xrNegotiateLoaderRuntimeInterface>(
        GetProcAddress(lib, "xrNegotiateLoaderRuntimeInterface"));
    REQUIRE(negotiate != nullptr, "GetProcAddress(xrNegotiateLoaderRuntimeInterface)");

    XrNegotiateLoaderInfo loaderInfo = {};
    loaderInfo.structType = XR_LOADER_INTERFACE_STRUCT_LOADER_INFO;
    loaderInfo.structVersion = XR_LOADER_INFO_STRUCT_VERSION;
    loaderInfo.structSize = sizeof(loaderInfo);
    loaderInfo.minInterfaceVersion = XR_CURRENT_LOADER_RUNTIME_VERSION;
    loaderInfo.maxInterfaceVersion = XR_CURRENT_LOADER_RUNTIME_VERSION;
    loaderInfo.minApiVersion = XR_MAKE_VERSION(1, 0, 0);
    loaderInfo.maxApiVersion = XR_MAKE_VERSION(1, 1, 99);

    XrNegotiateRuntimeRequest req = {};
    req.structType = XR_LOADER_INTERFACE_STRUCT_RUNTIME_REQUEST;
    req.structVersion = XR_RUNTIME_INFO_STRUCT_VERSION;
    req.structSize = sizeof(req);
    REQUIRE(negotiate(&loaderInfo, &req) == XR_SUCCESS, "xrNegotiateLoaderRuntimeInterface");
    REQUIRE(req.getInstanceProcAddr != nullptr, "negotiate: getInstanceProcAddr");
    g_gipa = req.getInstanceProcAddr;
    std::printf("negotiated: runtimeInterfaceVersion=%u\n", req.runtimeInterfaceVersion);

    // --- 2. Extensions + instance (enabling XR_KHR_D3D11_enable + XR_EXT_hand_tracking). ------------
    auto xrEnumerateInstanceExtensionProperties =
        get<PFN_xrEnumerateInstanceExtensionProperties>("xrEnumerateInstanceExtensionProperties");
    uint32_t extCount = 0;
    XRCHECK(xrEnumerateInstanceExtensionProperties(nullptr, 0, &extCount, nullptr));
    std::vector<XrExtensionProperties> exts(extCount, {XR_TYPE_EXTENSION_PROPERTIES});
    XRCHECK(xrEnumerateInstanceExtensionProperties(nullptr, extCount, &extCount, exts.data()));
    std::printf("extensions (%u):", extCount);
    for (uint32_t i = 0; i < extCount; ++i) std::printf(" %s", exts[i].extensionName);
    std::printf("\n");

    auto xrCreateInstance = get<PFN_xrCreateInstance>("xrCreateInstance");
    const char* wantedExts[] = {XR_KHR_D3D11_ENABLE_EXTENSION_NAME,
                                XR_EXT_HAND_TRACKING_EXTENSION_NAME};
    XrInstanceCreateInfo ici = {XR_TYPE_INSTANCE_CREATE_INFO};
    ici.enabledExtensionCount = 2;
    ici.enabledExtensionNames = wantedExts;
    std::strncpy(ici.applicationInfo.applicationName, "xrw_hands", XR_MAX_APPLICATION_NAME_SIZE - 1);
    std::strncpy(ici.applicationInfo.engineName, "xrwired", XR_MAX_ENGINE_NAME_SIZE - 1);
    ici.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
    XRCHECK(xrCreateInstance(&ici, &g_instance));
    REQUIRE(g_instance != XR_NULL_HANDLE, "xrCreateInstance handle");

    // With the instance up, resolve the rest of the entry points we need for the frame loop.
    auto xrGetSystem = get<PFN_xrGetSystem>("xrGetSystem");
    auto xrGetSystemProperties = get<PFN_xrGetSystemProperties>("xrGetSystemProperties");
    auto xrGetD3D11GraphicsRequirementsKHR =
        get<PFN_xrGetD3D11GraphicsRequirementsKHR>("xrGetD3D11GraphicsRequirementsKHR");
    auto xrCreateSession = get<PFN_xrCreateSession>("xrCreateSession");
    auto xrCreateReferenceSpace = get<PFN_xrCreateReferenceSpace>("xrCreateReferenceSpace");
    auto xrEnumerateViewConfigurationViews =
        get<PFN_xrEnumerateViewConfigurationViews>("xrEnumerateViewConfigurationViews");
    auto xrEnumerateSwapchainFormats = get<PFN_xrEnumerateSwapchainFormats>("xrEnumerateSwapchainFormats");
    auto xrCreateSwapchain = get<PFN_xrCreateSwapchain>("xrCreateSwapchain");
    auto xrEnumerateSwapchainImages = get<PFN_xrEnumerateSwapchainImages>("xrEnumerateSwapchainImages");
    auto xrPollEvent = get<PFN_xrPollEvent>("xrPollEvent");
    auto xrBeginSession = get<PFN_xrBeginSession>("xrBeginSession");
    auto xrEndSession = get<PFN_xrEndSession>("xrEndSession");
    auto xrWaitFrame = get<PFN_xrWaitFrame>("xrWaitFrame");
    auto xrBeginFrame = get<PFN_xrBeginFrame>("xrBeginFrame");
    auto xrEndFrame = get<PFN_xrEndFrame>("xrEndFrame");
    auto xrLocateViews = get<PFN_xrLocateViews>("xrLocateViews");
    auto xrAcquireSwapchainImage = get<PFN_xrAcquireSwapchainImage>("xrAcquireSwapchainImage");
    auto xrWaitSwapchainImage = get<PFN_xrWaitSwapchainImage>("xrWaitSwapchainImage");
    auto xrReleaseSwapchainImage = get<PFN_xrReleaseSwapchainImage>("xrReleaseSwapchainImage");
    auto xrDestroySwapchain = get<PFN_xrDestroySwapchain>("xrDestroySwapchain");
    auto xrDestroySpace = get<PFN_xrDestroySpace>("xrDestroySpace");
    auto xrDestroySession = get<PFN_xrDestroySession>("xrDestroySession");
    auto xrDestroyInstance = get<PFN_xrDestroyInstance>("xrDestroyInstance");

    // XR_EXT_hand_tracking entry points (resolved through the runtime's gipa like everything else).
    auto xrCreateHandTrackerEXT = get<PFN_xrCreateHandTrackerEXT>("xrCreateHandTrackerEXT");
    auto xrLocateHandJointsEXT = get<PFN_xrLocateHandJointsEXT>("xrLocateHandJointsEXT");
    auto xrDestroyHandTrackerEXT = get<PFN_xrDestroyHandTrackerEXT>("xrDestroyHandTrackerEXT");

    // --- 3. System (HMD form factor) + properties. -------------------------------------------------
    XrSystemGetInfo sgi = {XR_TYPE_SYSTEM_GET_INFO};
    sgi.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    XrSystemId system = XR_NULL_SYSTEM_ID;
    XRCHECK(xrGetSystem(g_instance, &sgi, &system));
    REQUIRE(system != XR_NULL_SYSTEM_ID, "xrGetSystem id");
    XrSystemProperties sysProps = {XR_TYPE_SYSTEM_PROPERTIES};
    XRCHECK(xrGetSystemProperties(g_instance, system, &sysProps));
    std::printf("system: %s\n", sysProps.systemName);

    // --- 4. D3D11 graphics requirements (spec requires the call before session create). -------------
    XrGraphicsRequirementsD3D11KHR gfxReq = {XR_TYPE_GRAPHICS_REQUIREMENTS_D3D11_KHR};
    XRCHECK(xrGetD3D11GraphicsRequirementsKHR(g_instance, system, &gfxReq));
    std::printf("d3d11 graphics req: minFeatureLevel=0x%x\n", unsigned(gfxReq.minFeatureLevel));

    // --- 5. Session bound to our D3D11 device via XrGraphicsBindingD3D11KHR. -------------------------
    XrGraphicsBindingD3D11KHR binding = {XR_TYPE_GRAPHICS_BINDING_D3D11_KHR};
    binding.device = d3dDevice;
    XrSessionCreateInfo sci = {XR_TYPE_SESSION_CREATE_INFO};
    sci.next = &binding;
    sci.systemId = system;
    XrSession session = XR_NULL_HANDLE;
    XRCHECK(xrCreateSession(g_instance, &sci, &session));

    // --- 6. LOCAL reference space with an identity pose. --------------------------------------------
    XrReferenceSpaceCreateInfo rsci = {XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    rsci.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
    rsci.poseInReferenceSpace.orientation.w = 1.0f;   // identity
    XrSpace localSpace = XR_NULL_HANDLE;
    XRCHECK(xrCreateReferenceSpace(session, &rsci, &localSpace));

    // --- 6b. One hand tracker per hand (left/right), default 26-joint set. ---------------------------
    XrHandTrackerEXT handTrackers[2] = {XR_NULL_HANDLE, XR_NULL_HANDLE};
    const XrHandEXT handSides[2] = {XR_HAND_LEFT_EXT, XR_HAND_RIGHT_EXT};
    for (int h = 0; h < 2; ++h) {
        XrHandTrackerCreateInfoEXT htci = {XR_TYPE_HAND_TRACKER_CREATE_INFO_EXT};
        htci.hand = handSides[h];
        htci.handJointSet = XR_HAND_JOINT_SET_DEFAULT_EXT;
        XRCHECK(xrCreateHandTrackerEXT(session, &htci, &handTrackers[h]));
    }
    std::printf("hand trackers created (left+right, %d joints each)\n", XR_HAND_JOINT_COUNT_EXT);

    // --- 7. Stereo view configuration -> 2 views, per-eye recommended resolution. -------------------
    uint32_t viewCount = 0;
    XRCHECK(xrEnumerateViewConfigurationViews(g_instance, system,
                                              XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, 0, &viewCount,
                                              nullptr));
    REQUIRE(viewCount == 2, "stereo view count");
    std::vector<XrViewConfigurationView> configViews(viewCount, {XR_TYPE_VIEW_CONFIGURATION_VIEW});
    XRCHECK(xrEnumerateViewConfigurationViews(g_instance, system,
                                              XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, viewCount,
                                              &viewCount, configViews.data()));
    std::printf("stereo views=%u, per-eye %ux%u\n", viewCount,
                configViews[0].recommendedImageRectWidth, configViews[0].recommendedImageRectHeight);

    // --- 8. Pick a swapchain format, then one swapchain + RTVs per eye. -----------------------------
    uint32_t formatCount = 0;
    XRCHECK(xrEnumerateSwapchainFormats(session, 0, &formatCount, nullptr));
    std::vector<int64_t> formats(formatCount);
    XRCHECK(xrEnumerateSwapchainFormats(session, formatCount, &formatCount, formats.data()));
    int64_t chosenFormat = formatCount > 0 ? formats[0] : DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
    for (int64_t f : formats) {
        if (f == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB) {   // sRGB preferred so our colors are not washed out
            chosenFormat = f;
            break;
        }
    }
    std::printf("swapchain format 0x%llx\n", (unsigned long long)chosenFormat);

    EyeSwapchain eyes[2];
    for (int eye = 0; eye < 2; ++eye) {
        EyeSwapchain& sc = eyes[eye];
        sc.width = configViews[eye].recommendedImageRectWidth;
        sc.height = configViews[eye].recommendedImageRectHeight;
        XrSwapchainCreateInfo scci = {XR_TYPE_SWAPCHAIN_CREATE_INFO};
        scci.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT;
        scci.format = chosenFormat;
        scci.sampleCount = 1;
        scci.width = sc.width;
        scci.height = sc.height;
        scci.faceCount = 1;
        scci.arraySize = 1;
        scci.mipCount = 1;
        XRCHECK(xrCreateSwapchain(session, &scci, &sc.handle));

        uint32_t imageCount = 0;
        XRCHECK(xrEnumerateSwapchainImages(sc.handle, 0, &imageCount, nullptr));
        std::vector<XrSwapchainImageD3D11KHR> images(imageCount, {XR_TYPE_SWAPCHAIN_IMAGE_D3D11_KHR});
        XRCHECK(xrEnumerateSwapchainImages(
            sc.handle, imageCount, &imageCount,
            reinterpret_cast<XrSwapchainImageBaseHeader*>(images.data())));
        sc.textures.resize(imageCount);
        sc.rtvs.resize(imageCount);
        for (uint32_t i = 0; i < imageCount; ++i) {
            sc.textures[i] = images[i].texture;
            D3D11_RENDER_TARGET_VIEW_DESC rtvDesc = {};
            rtvDesc.Format = DXGI_FORMAT(chosenFormat);
            rtvDesc.ViewDimension = D3D11_RTV_DIMENSION_TEXTURE2D;
            rtvDesc.Texture2D.MipSlice = 0;
            hr = d3dDevice->CreateRenderTargetView(sc.textures[i], &rtvDesc, &sc.rtvs[i]);
            REQUIRE(SUCCEEDED(hr), "CreateRenderTargetView");
        }
        std::printf("eye %d: %ux%u, %u swapchain images\n", eye, sc.width, sc.height, imageCount);
    }

    // --- 8b. Quad pipeline: pass-through VS/PS, input layout, color cbuffer, dynamic vertex buffer. --
    ID3DBlob* vsBlob = compileShader(kQuadShaderHLSL, "VSMain", "vs_5_0");
    ID3DBlob* psBlob = compileShader(kQuadShaderHLSL, "PSMain", "ps_5_0");
    ID3D11VertexShader* quadVS = nullptr;
    ID3D11PixelShader* quadPS = nullptr;
    REQUIRE(SUCCEEDED(d3dDevice->CreateVertexShader(vsBlob->GetBufferPointer(), vsBlob->GetBufferSize(),
                                                    nullptr, &quadVS)),
            "CreateVertexShader");
    REQUIRE(SUCCEEDED(d3dDevice->CreatePixelShader(psBlob->GetBufferPointer(), psBlob->GetBufferSize(),
                                                   nullptr, &quadPS)),
            "CreatePixelShader");

    D3D11_INPUT_ELEMENT_DESC inputDesc[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0}};
    ID3D11InputLayout* quadLayout = nullptr;
    REQUIRE(SUCCEEDED(d3dDevice->CreateInputLayout(inputDesc, 1, vsBlob->GetBufferPointer(),
                                                   vsBlob->GetBufferSize(), &quadLayout)),
            "CreateInputLayout");
    vsBlob->Release();
    psBlob->Release();

    // No back-face culling: the quads are simple NDC triangles whose winding would otherwise be
    // culled by D3D's default state, leaving nothing rasterized (only the clear would show).
    ID3D11RasterizerState* noCull = nullptr;
    {
        D3D11_RASTERIZER_DESC rd = {};
        rd.FillMode = D3D11_FILL_SOLID;
        rd.CullMode = D3D11_CULL_NONE;
        rd.DepthClipEnable = TRUE;
        REQUIRE(SUCCEEDED(d3dDevice->CreateRasterizerState(&rd, &noCull)), "CreateRasterizerState");
    }

    // Per-draw color constant buffer (float4, 16 bytes).
    ID3D11Buffer* colorCB = nullptr;
    {
        D3D11_BUFFER_DESC cbDesc = {};
        cbDesc.ByteWidth = 16;
        cbDesc.Usage = D3D11_USAGE_DYNAMIC;
        cbDesc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        cbDesc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        REQUIRE(SUCCEEDED(d3dDevice->CreateBuffer(&cbDesc, nullptr, &colorCB)), "CreateBuffer(colorCB)");
    }

    // Dynamic vertex buffer big enough for one hand's worth of joint quads (26 joints * 6 verts * 2f).
    const uint32_t kMaxVerts = XR_HAND_JOINT_COUNT_EXT * 6;
    ID3D11Buffer* quadVB = nullptr;
    {
        D3D11_BUFFER_DESC vbDesc = {};
        vbDesc.ByteWidth = kMaxVerts * 2 * sizeof(float);
        vbDesc.Usage = D3D11_USAGE_DYNAMIC;
        vbDesc.BindFlags = D3D11_BIND_VERTEX_BUFFER;
        vbDesc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        REQUIRE(SUCCEEDED(d3dDevice->CreateBuffer(&vbDesc, nullptr, &quadVB)), "CreateBuffer(quadVB)");
    }

    // Per-hand draw color: left = green, right = cyan.
    const float handColors[2][4] = {{0.10f, 0.90f, 0.20f, 1.0f}, {0.10f, 0.85f, 0.95f, 1.0f}};

    // Reusable per-frame joint storage.
    XrHandJointLocationEXT jointLocs[2][XR_HAND_JOINT_COUNT_EXT];
    XrBool32 handActive[2] = {XR_FALSE, XR_FALSE};

    // --- fps timer for the periodic progress line. --------------------------------------------------
    LARGE_INTEGER perfFreq, tPrev;
    QueryPerformanceFrequency(&perfFreq);
    QueryPerformanceCounter(&tPrev);

    // --- 9./10. Event + frame loop. ----------------------------------------------------------------
    bool running = true;
    bool sessionRunning = false;
    XrSessionState state = XR_SESSION_STATE_UNKNOWN;
    int frame = 0;

    while (running) {
        // Drain events: drive begin/end session off session-state changes, exit on EXITING/LOSS.
        XrEventDataBuffer ev = {XR_TYPE_EVENT_DATA_BUFFER};
        XrResult pollResult;
        while ((pollResult = xrPollEvent(g_instance, &ev)) == XR_SUCCESS) {
            if (ev.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                state = reinterpret_cast<XrEventDataSessionStateChanged*>(&ev)->state;
                std::printf("session state -> %d\n", int(state));
                if (state == XR_SESSION_STATE_READY) {
                    XrSessionBeginInfo begin = {XR_TYPE_SESSION_BEGIN_INFO};
                    begin.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                    XRCHECK(xrBeginSession(session, &begin));
                    sessionRunning = true;
                } else if (state == XR_SESSION_STATE_STOPPING) {
                    XRCHECK(xrEndSession(session));
                    sessionRunning = false;
                } else if (state == XR_SESSION_STATE_EXITING ||
                           state == XR_SESSION_STATE_LOSS_PENDING) {
                    running = false;
                }
            }
            ev = {XR_TYPE_EVENT_DATA_BUFFER};
        }
        if (pollResult != XR_EVENT_UNAVAILABLE) {
            std::printf("FAIL: xrPollEvent = %d\n", int(pollResult));
            std::exit(1);
        }

        if (!sessionRunning) {
            Sleep(10);
            continue;
        }

        // xrWaitFrame -> xrBeginFrame -> render if asked -> xrEndFrame.
        XrFrameWaitInfo waitInfo = {XR_TYPE_FRAME_WAIT_INFO};
        XrFrameState frameState = {XR_TYPE_FRAME_STATE};
        XRCHECK(xrWaitFrame(session, &waitInfo, &frameState));
        XrFrameBeginInfo beginInfo = {XR_TYPE_FRAME_BEGIN_INFO};
        XRCHECK(xrBeginFrame(session, &beginInfo));

        XrCompositionLayerProjectionView projViews[2] = {};
        XrCompositionLayerProjection layer = {XR_TYPE_COMPOSITION_LAYER_PROJECTION};
        bool rendered = false;
        handActive[0] = handActive[1] = XR_FALSE;

        if (frameState.shouldRender == XR_TRUE) {
            // Locate the two eye views for the predicted display time in LOCAL space.
            XrViewState viewState = {XR_TYPE_VIEW_STATE};
            XrViewLocateInfo locate = {XR_TYPE_VIEW_LOCATE_INFO};
            locate.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
            locate.displayTime = frameState.predictedDisplayTime;
            locate.space = localSpace;
            uint32_t located = 0;
            XrView views[2] = {{XR_TYPE_VIEW}, {XR_TYPE_VIEW}};
            XRCHECK(xrLocateViews(session, &locate, &viewState, 2, &located, views));

            // Locate all 26 joints of each hand in LOCAL space at the predicted display time.
            for (int h = 0; h < 2; ++h) {
                XrHandJointsLocateInfoEXT li = {XR_TYPE_HAND_JOINTS_LOCATE_INFO_EXT};
                li.baseSpace = localSpace;
                li.time = frameState.predictedDisplayTime;
                XrHandJointLocationsEXT locs = {XR_TYPE_HAND_JOINT_LOCATIONS_EXT};
                locs.jointCount = XR_HAND_JOINT_COUNT_EXT;
                locs.jointLocations = jointLocs[h];
                XRCHECK(xrLocateHandJointsEXT(handTrackers[h], &li, &locs));
                handActive[h] = locs.isActive;
            }

            for (int eye = 0; eye < 2; ++eye) {
                EyeSwapchain& sc = eyes[eye];
                uint32_t imageIndex = 0;
                XrSwapchainImageAcquireInfo acquire = {XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
                XRCHECK(xrAcquireSwapchainImage(sc.handle, &acquire, &imageIndex));
                XrSwapchainImageWaitInfo scWait = {XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
                scWait.timeout = XR_INFINITE_DURATION;
                XRCHECK(xrWaitSwapchainImage(sc.handle, &scWait));

                ID3D11RenderTargetView* rtv = sc.rtvs[imageIndex];
                d3dContext->OMSetRenderTargets(1, &rtv, nullptr);
                const float clearColor[4] = {0.08f, 0.08f, 0.10f, 1.0f};   // dark grey
                d3dContext->ClearRenderTargetView(rtv, clearColor);

                D3D11_VIEWPORT vp = {};
                vp.Width = float(sc.width);
                vp.Height = float(sc.height);
                vp.MaxDepth = 1.0f;
                d3dContext->RSSetViewports(1, &vp);

                // Square marker size: ~2.5% of viewport height in NDC (full NDC height is 2.0), with
                // an aspect correction so it stays square in pixels.
                const float halfY = 0.018f;
                const float aspect = sc.height > 0 ? float(sc.width) / float(sc.height) : 1.0f;
                const float halfX = halfY / aspect;

                // Common pipeline state for the quad draws.
                d3dContext->IASetInputLayout(quadLayout);
                d3dContext->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
                d3dContext->VSSetShader(quadVS, nullptr, 0);
                d3dContext->PSSetShader(quadPS, nullptr, 0);
                d3dContext->RSSetState(noCull);

                // Always-visible markers so we can confirm we are looking at OUR view (not the system
                // shell): a yellow center crosshair + four corner squares. Drawn every frame/eye.
                {
                    std::vector<float> mk;
                    auto rect = [&](float cx, float cy, float hx, float hy) {
                        const float x0 = cx - hx, x1 = cx + hx, y0 = cy - hy, y1 = cy + hy;
                        const float q[12] = {x0, y0, x1, y0, x1, y1, x0, y0, x1, y1, x0, y1};
                        mk.insert(mk.end(), q, q + 12);
                    };
                    rect(0.f, 0.f, 0.30f, 0.012f);   // horizontal bar
                    rect(0.f, 0.f, 0.012f, 0.40f);   // vertical bar
                    rect(-0.9f, 0.85f, 0.05f, 0.06f);
                    rect(0.9f, 0.85f, 0.05f, 0.06f);
                    rect(-0.9f, -0.85f, 0.05f, 0.06f);
                    rect(0.9f, -0.85f, 0.05f, 0.06f);
                    D3D11_MAPPED_SUBRESOURCE m = {};
                    if (SUCCEEDED(d3dContext->Map(quadVB, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
                        std::memcpy(m.pData, mk.data(), mk.size() * sizeof(float));
                        d3dContext->Unmap(quadVB, 0);
                    }
                    const float yellow[4] = {1.0f, 0.85f, 0.1f, 1.0f};
                    if (SUCCEEDED(d3dContext->Map(colorCB, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
                        std::memcpy(m.pData, yellow, sizeof(yellow));
                        d3dContext->Unmap(colorCB, 0);
                    }
                    d3dContext->PSSetConstantBuffers(0, 1, &colorCB);
                    const UINT st = 2 * sizeof(float), of = 0;
                    d3dContext->IASetVertexBuffers(0, 1, &quadVB, &st, &of);
                    d3dContext->Draw(UINT(mk.size() / 2), 0);
                }

                for (int h = 0; h < 2; ++h) {
                    if (!handActive[h]) continue;

                    // Build this hand's joint quads in NDC on the CPU (6 verts per valid joint).
                    std::vector<float> verts;
                    verts.reserve(kMaxVerts * 2);
                    for (int j = 0; j < XR_HAND_JOINT_COUNT_EXT; ++j) {
                        if (!(jointLocs[h][j].locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT))
                            continue;
                        float nx, ny;
                        if (!projectJoint(views[eye], jointLocs[h][j].pose.position, nx, ny)) continue;
                        const float x0 = nx - halfX, x1 = nx + halfX;
                        const float y0 = ny - halfY, y1 = ny + halfY;
                        const float quad[12] = {x0, y0, x1, y0, x1, y1, x0, y0, x1, y1, x0, y1};
                        verts.insert(verts.end(), quad, quad + 12);
                    }
                    if (verts.empty()) continue;

                    D3D11_MAPPED_SUBRESOURCE mapped = {};
                    if (SUCCEEDED(d3dContext->Map(quadVB, 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped))) {
                        std::memcpy(mapped.pData, verts.data(), verts.size() * sizeof(float));
                        d3dContext->Unmap(quadVB, 0);
                    }
                    if (SUCCEEDED(d3dContext->Map(colorCB, 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped))) {
                        std::memcpy(mapped.pData, handColors[h], sizeof(handColors[h]));
                        d3dContext->Unmap(colorCB, 0);
                    }
                    d3dContext->PSSetConstantBuffers(0, 1, &colorCB);

                    const UINT stride = 2 * sizeof(float);
                    const UINT offset = 0;
                    d3dContext->IASetVertexBuffers(0, 1, &quadVB, &stride, &offset);
                    d3dContext->Draw(UINT(verts.size() / 2), 0);
                }

                XrSwapchainImageReleaseInfo release = {XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
                XRCHECK(xrReleaseSwapchainImage(sc.handle, &release));

                projViews[eye] = {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW};
                projViews[eye].pose = views[eye].pose;
                projViews[eye].fov = views[eye].fov;
                projViews[eye].subImage.swapchain = sc.handle;
                projViews[eye].subImage.imageRect.offset = {0, 0};
                projViews[eye].subImage.imageRect.extent = {int32_t(sc.width), int32_t(sc.height)};
                projViews[eye].subImage.imageArrayIndex = 0;
            }
            layer.space = localSpace;
            layer.viewCount = 2;
            layer.views = projViews;
            rendered = true;
        }

        const XrCompositionLayerBaseHeader* layers[] = {
            reinterpret_cast<XrCompositionLayerBaseHeader*>(&layer)};
        XrFrameEndInfo endInfo = {XR_TYPE_FRAME_END_INFO};
        endInfo.displayTime = frameState.predictedDisplayTime;
        endInfo.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
        endInfo.layerCount = rendered ? 1 : 0;
        endInfo.layers = rendered ? layers : nullptr;
        XRCHECK(xrEndFrame(session, &endInfo));

        ++frame;
        if (frame % 72 == 0) {
            LARGE_INTEGER tNow;
            QueryPerformanceCounter(&tNow);
            double secs = double(tNow.QuadPart - tPrev.QuadPart) / double(perfFreq.QuadPart);
            tPrev = tNow;
            // Palm is joint index 0 (XR_HAND_JOINT_PALM_EXT); report tracking without the headset view.
            const XrVector3f& lp = jointLocs[0][XR_HAND_JOINT_PALM_EXT].pose.position;
            std::printf("frame %d, %.1f fps | L active=%d R active=%d | L palm=(%.3f, %.3f, %.3f)\n",
                        frame, secs > 0.0 ? 72.0 / secs : 0.0, int(handActive[0]), int(handActive[1]),
                        lp.x, lp.y, lp.z);
        }
        if (frame >= maxFrames) running = false;   // --frames N cap (default 600)
    }

    // --- 11. Teardown: pipeline -> hand trackers -> swapchains (+ RTVs) -> space -> session ->
    //         instance -> D3D11. -----------------------------------------------------------------
    if (quadVB) quadVB->Release();
    if (colorCB) colorCB->Release();
    if (quadLayout) quadLayout->Release();
    if (quadPS) quadPS->Release();
    if (quadVS) quadVS->Release();
    for (int h = 0; h < 2; ++h) {
        if (handTrackers[h] != XR_NULL_HANDLE) xrDestroyHandTrackerEXT(handTrackers[h]);
    }
    for (int eye = 0; eye < 2; ++eye) {
        for (ID3D11RenderTargetView* rtv : eyes[eye].rtvs) {
            if (rtv) rtv->Release();
        }
        if (eyes[eye].handle != XR_NULL_HANDLE) xrDestroySwapchain(eyes[eye].handle);
    }
    if (localSpace != XR_NULL_HANDLE) xrDestroySpace(localSpace);
    if (session != XR_NULL_HANDLE) xrDestroySession(session);
    if (g_instance != XR_NULL_HANDLE) xrDestroyInstance(g_instance);
    if (d3dContext) d3dContext->Release();
    if (d3dDevice) d3dDevice->Release();

    std::printf("HANDS DONE\n");
    return 0;
}
