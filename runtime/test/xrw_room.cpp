// xrw_room - a full D3D11 OpenXR app that drives OUR runtime end to end and renders a head-tracked 3D
// room, so the wearer can look around a stable environment (a floor grid, an enclosing room, and a few
// solid colored cubes at different depths for obvious stereo/parallax).
//
// It is the 3D-scene sibling of xrw_probe.cpp / xrw_hands.cpp: same runtime-direct harness (negotiate
// the runtime interface with no official loader, create a session against a real ID3D11Device, run a
// wait/begin/locate/acquire/release/end frame loop). On top of the probe it adds a per-image depth
// buffer, real vertex/pixel shaders, a scene of static geometry, and per-eye view/projection matrices
// built from XrView.pose / XrView.fov.
//
// MATRIX / SHADER CONVENTION (documented so there are no handedness bugs):
//   - Row-vector convention throughout: a point is a row vector [x y z 1] and is transformed as
//     p' = p * M. Chained transforms compose left-to-right: p_clip = p_model * (Model * View * Proj).
//     Matrices are stored ROW-MAJOR in a float[16] with m[row*4 + col].
//   - The HLSL constant buffer declares `row_major float4x4 gMVP` and the vertex shader does
//     mul(float4(pos,1), gMVP), which is exactly the row-vector product sum_k v[k]*M[k][j] with the
//     same row-major memory layout we upload from the CPU. No transpose needed.
//   - VIEW (world -> camera) is the inverse of the eye's rigid pose. With R the rotation of
//     view.pose.orientation and e = view.pose.position, camera-space c = R^T * (p - e) (eye looks down
//     -Z). In row-vector form the 3x3 block of View is R itself (stored row-major) and the 4th row is
//     -(R^T * e); see makeView().
//   - PROJECTION comes straight from view.fov tangents (asymmetric OpenXR frustum, D3D clip z in
//     [0,1]), near=0.05, far=100, using the exact m00/m11/m20/m21/m22/m23/m32 given in the spec; see
//     makeProj(). A point straight ahead at (0,0,-1) maps to clip w=1>0 and ndc z ~0.95 (inside [0,1]).
//
// Success bar for now is a clean compile with the correct call sequence; it is meant to be worn later.
#define _CRT_SECURE_NO_WARNINGS   // strncpy into the fixed-size OpenXR name fields is intentional
#include <d3d11.h>          // must precede openxr_platform.h so ID3D11Device/Texture2D are defined
#include <d3d11_1.h>        // kept for parity with the probe
#include <d3dcompiler.h>    // D3DCompile for the scene shaders
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

// One eye's swapchain plus, per swapchain image, a render target view and a matching depth buffer +
// depth-stencil view so the 3D scene gets correct occlusion.
struct EyeSwapchain {
    XrSwapchain handle = XR_NULL_HANDLE;
    uint32_t width = 0, height = 0;
    std::vector<ID3D11Texture2D*> textures;
    std::vector<ID3D11RenderTargetView*> rtvs;
    std::vector<ID3D11Texture2D*> depthTextures;
    std::vector<ID3D11DepthStencilView*> dsvs;
};

// ---- Row-vector, row-major 4x4 helpers (m[row*4 + col]). ----------------------------------------
struct Mat4 { float m[16]; };

Mat4 identity() {
    Mat4 r{};
    r.m[0] = r.m[5] = r.m[10] = r.m[15] = 1.0f;
    return r;
}

// C = A * B in the row-vector sense: C[i][j] = sum_k A[i][k] * B[k][j].
Mat4 mul(const Mat4& a, const Mat4& b) {
    Mat4 c{};
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j) {
            float s = 0.0f;
            for (int k = 0; k < 4; ++k) s += a.m[i * 4 + k] * b.m[k * 4 + j];
            c.m[i * 4 + j] = s;
        }
    return c;
}

// Model matrix that scales uniformly then translates (row-vector: p_world = p_model * M).
Mat4 makeModel(float sx, float sy, float sz, float tx, float ty, float tz) {
    Mat4 r = identity();
    r.m[0] = sx;   r.m[5] = sy;   r.m[10] = sz;
    r.m[12] = tx;  r.m[13] = ty;  r.m[14] = tz;   // 4th row = translation
    return r;
}

// Scale, spin about Y by `ang` radians, then translate (row-vector: p_world = p_model * M). Used to
// animate the cubes so the scene has real motion without the wearer moving their head — this keeps the
// video encoder honestly loaded (changing content every frame) during perf runs.
Mat4 makeModelSpin(float s, float ang, float tx, float ty, float tz) {
    const float c = std::cos(ang), sn = std::sin(ang);
    Mat4 r = identity();
    r.m[0] = s * c;    r.m[2] = -s * sn;
    r.m[5] = s;
    r.m[8] = s * sn;   r.m[10] = s * c;
    r.m[12] = tx;      r.m[13] = ty;      r.m[14] = tz;
    return r;
}

// VIEW (world -> camera): c = R^T * (p - e). In row-vector form the 3x3 block is R (row-major) and the
// 4th row is -(R^T * e). R is built from the eye orientation quaternion.
Mat4 makeView(const XrView& view) {
    const XrQuaternionf& q = view.pose.orientation;
    const float x = q.x, y = q.y, z = q.z, w = q.w;
    const float r00 = 1.f - 2.f * (y * y + z * z);
    const float r01 = 2.f * (x * y - w * z);
    const float r02 = 2.f * (x * z + w * y);
    const float r10 = 2.f * (x * y + w * z);
    const float r11 = 1.f - 2.f * (x * x + z * z);
    const float r12 = 2.f * (y * z - w * x);
    const float r20 = 2.f * (x * z - w * y);
    const float r21 = 2.f * (y * z + w * x);
    const float r22 = 1.f - 2.f * (x * x + y * y);

    const float ex = view.pose.position.x, ey = view.pose.position.y, ez = view.pose.position.z;

    Mat4 v{};
    // Top-left 3x3 = R (row-major), so [x y z]*V picks R^T columns -> gives R^T*(p) for the linear part.
    v.m[0] = r00;  v.m[1] = r01;  v.m[2] = r02;   v.m[3] = 0.f;
    v.m[4] = r10;  v.m[5] = r11;  v.m[6] = r12;   v.m[7] = 0.f;
    v.m[8] = r20;  v.m[9] = r21;  v.m[10] = r22;  v.m[11] = 0.f;
    // 4th row = -(R^T * e).
    v.m[12] = -(r00 * ex + r10 * ey + r20 * ez);
    v.m[13] = -(r01 * ex + r11 * ey + r21 * ez);
    v.m[14] = -(r02 * ex + r12 * ey + r22 * ez);
    v.m[15] = 1.f;
    return v;
}

// PROJECTION from the fov half-angle tangents (asymmetric OpenXR frustum, D3D clip z in [0,1]).
// Row-vector layout: clip = c * P, so P[row][col] = the m<row><col> entries from the spec.
Mat4 makeProj(const XrFovf& fov, float nearZ, float farZ) {
    const float tanL = std::tan(fov.angleLeft);
    const float tanR = std::tan(fov.angleRight);
    const float tanU = std::tan(fov.angleUp);
    const float tanD = std::tan(fov.angleDown);
    const float tanW = tanR - tanL;
    const float tanH = tanU - tanD;

    const float m00 = 2.f / tanW;
    const float m11 = 2.f / tanH;
    const float m20 = (tanR + tanL) / tanW;
    const float m21 = (tanU + tanD) / tanH;
    const float m22 = farZ / (nearZ - farZ);
    const float m23 = -1.f;
    const float m32 = (nearZ * farZ) / (nearZ - farZ);

    Mat4 p{};
    p.m[0] = m00;                                  // row0
    p.m[5] = m11;                                  // row1
    p.m[8] = m20;  p.m[9] = m21;  p.m[10] = m22;  p.m[11] = m23;   // row2
    p.m[14] = m32;                                 // row3
    return p;
}

// A scene vertex: position + per-vertex color.
struct Vertex {
    float px, py, pz;
    float r, g, b;
};

void pushQuad(std::vector<Vertex>& v, const float p0[3], const float p1[3], const float p2[3],
              const float p3[3], float r, float g, float b) {
    auto V = [&](const float* p) { v.push_back({p[0], p[1], p[2], r, g, b}); };
    V(p0); V(p1); V(p2);   // triangle 1
    V(p0); V(p2); V(p3);   // triangle 2
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

// VS applies the row_major MVP (b0) to POSITION and passes per-vertex COLOR through; PS multiplies the
// interpolated color by a per-draw tint (b1).
const char* kSceneShaderHLSL =
    "cbuffer MVPCB  : register(b0) { row_major float4x4 gMVP; };\n"
    "cbuffer TintCB : register(b1) { float4 gTint; };\n"
    "struct VSIn  { float3 pos : POSITION; float3 col : COLOR; };\n"
    "struct VSOut { float4 pos : SV_POSITION; float3 col : COLOR; };\n"
    "VSOut VSMain(VSIn i) {\n"
    "    VSOut o;\n"
    "    o.pos = mul(float4(i.pos, 1.0), gMVP);\n"
    "    o.col = i.col;\n"
    "    return o;\n"
    "}\n"
    "float4 PSMain(VSOut i) : SV_TARGET { return float4(i.col * gTint.rgb, 1.0); }\n";

// Full-screen textured blit (no vertex buffer): the VS synthesizes a covering triangle from SV_VertexID
// exactly like the runtime's blitter. corner = ((id<<1)&2, id&2) -> (0,0),(2,0),(0,2); clip pos =
// corner*2-1 covers [-1,1]; uv = corner but with v flipped (uv.y = 1 - corner.y) so a top-to-bottom
// row-major image is drawn upright (texel row 0 at the top of the eye). PS samples with a linear-clamp
// sampler.
const char* kCalibShaderHLSL =
    "Texture2D    gTex : register(t0);\n"
    "SamplerState gSmp : register(s0);\n"
    "struct VSOut { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };\n"
    "VSOut VSCalib(uint id : SV_VertexID) {\n"
    "    float2 corner = float2((id << 1) & 2, id & 2);\n"
    "    VSOut o;\n"
    "    o.pos = float4(corner * 2.0 - 1.0, 0.0, 1.0);\n"
    "    o.uv  = float2(corner.x, 1.0 - corner.y);\n"   // v flip -> image upright
    "    return o;\n"
    "}\n"
    "float4 PSCalib(VSOut i) : SV_TARGET { return gTex.Sample(gSmp, i.uv); }\n";

// Full-screen animated per-pixel noise: worst case for the video encoder (near-incompressible, changes
// every frame), so the stream actually reaches the target bitrate and fps/latency are representative.
const char* kStressShaderHLSL =
    "struct VSOut { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };\n"
    "VSOut VSStress(uint id : SV_VertexID) {\n"
    "  float2 c = float2((id << 1) & 2, id & 2);\n"
    "  VSOut o; o.pos = float4(c * 2 - 1, 0, 1); o.uv = c; return o; }\n"
    "cbuffer Seed : register(b0) { uint gFrame; uint3 pad; };\n"
    "float h(float2 p, float f) {\n"
    "  float3 p3 = frac(float3(p.xyx) * 0.1031 + f * 0.137);\n"
    "  p3 += dot(p3, p3.yzx + 33.33);\n"
    "  return frac((p3.x + p3.y) * p3.z); }\n"
    "float4 PSStress(VSOut i) : SV_TARGET {\n"
    "  float2 c = i.pos.xy; float f = (float)gFrame;\n"
    "  return float4(h(c, f), h(c + 19.0, f), h(c + 43.0, f), 1); }\n";

}  // namespace

int main(int argc, char** argv) {
    const char* dllPath = "xrwired_runtime.dll";
    const char* calibPath = nullptr;   // --calib <path>: raw RGBA8 one-eye pattern; enables calib mode
    bool stressMode = false;           // --stress: full-frame animated noise (encoder load test)
    int maxFrames = 3600;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--frames") == 0 && i + 1 < argc) {
            maxFrames = std::atoi(argv[++i]);
        } else if (std::strcmp(argv[i], "--calib") == 0 && i + 1 < argc) {
            calibPath = argv[++i];   // full-screen calibration pattern instead of the 3D room
        } else if (std::strcmp(argv[i], "--stress") == 0) {
            stressMode = true;       // full-frame animated noise to load the encoder (perf testing)
        } else if (argv[i][0] != '-') {
            dllPath = argv[i];   // first positional arg is the runtime dll path
        }
    }
    const bool calibMode = (calibPath != nullptr);

    // --- 1. D3D11 device + context (hardware, feature level 11_0+, single-threaded is fine). --------
    ID3D11Device* d3dDevice = nullptr;
    ID3D11DeviceContext* d3dContext = nullptr;
    const D3D_FEATURE_LEVEL wantLevels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0};
    D3D_FEATURE_LEVEL gotLevel = D3D_FEATURE_LEVEL_11_0;
    // NOT single-threaded: the runtime encodes on its own worker thread and drives NVENC (which makes
    // internal D3D11 calls) against this device, so it must be free-threaded — as real game engines and
    // OpenComposite titles create it. A SINGLETHREADED device makes NVENC's map fail off the render thread.
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
                                   0, wantLevels, 2, D3D11_SDK_VERSION,
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

    // --- 2. Extensions + instance (enabling XR_KHR_D3D11_enable). -----------------------------------
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
    const char* wantedExts[] = {XR_KHR_D3D11_ENABLE_EXTENSION_NAME};
    XrInstanceCreateInfo ici = {XR_TYPE_INSTANCE_CREATE_INFO};
    ici.enabledExtensionCount = 1;
    ici.enabledExtensionNames = wantedExts;
    std::strncpy(ici.applicationInfo.applicationName, "xrw_room", XR_MAX_APPLICATION_NAME_SIZE - 1);
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

    // --- 8. Pick a swapchain format, then one swapchain + RTVs + depth per eye. ----------------------
    uint32_t formatCount = 0;
    XRCHECK(xrEnumerateSwapchainFormats(session, 0, &formatCount, nullptr));
    std::vector<int64_t> formats(formatCount);
    XRCHECK(xrEnumerateSwapchainFormats(session, formatCount, &formatCount, formats.data()));
    // Room (3D) wants sRGB for correct shading; calib streams an already-sRGB pattern verbatim, so it
    // needs a linear/UNORM target (raw store) or the bytes get sRGB-encoded a second time.
    const int64_t wantFormat = calibMode ? DXGI_FORMAT_R8G8B8A8_UNORM : DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
    int64_t chosenFormat = formatCount > 0 ? formats[0] : wantFormat;
    for (int64_t f : formats) {
        if (f == wantFormat) { chosenFormat = f; break; }
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
        sc.depthTextures.resize(imageCount);
        sc.dsvs.resize(imageCount);
        for (uint32_t i = 0; i < imageCount; ++i) {
            sc.textures[i] = images[i].texture;
            D3D11_RENDER_TARGET_VIEW_DESC rtvDesc = {};
            rtvDesc.Format = DXGI_FORMAT(chosenFormat);
            rtvDesc.ViewDimension = D3D11_RTV_DIMENSION_TEXTURE2D;
            rtvDesc.Texture2D.MipSlice = 0;
            hr = d3dDevice->CreateRenderTargetView(sc.textures[i], &rtvDesc, &sc.rtvs[i]);
            REQUIRE(SUCCEEDED(hr), "CreateRenderTargetView");

            // Matching depth buffer (D32_FLOAT) + DSV, one per swapchain image.
            D3D11_TEXTURE2D_DESC dtd = {};
            dtd.Width = sc.width;
            dtd.Height = sc.height;
            dtd.MipLevels = 1;
            dtd.ArraySize = 1;
            dtd.Format = DXGI_FORMAT_D32_FLOAT;
            dtd.SampleDesc.Count = 1;
            dtd.Usage = D3D11_USAGE_DEFAULT;
            dtd.BindFlags = D3D11_BIND_DEPTH_STENCIL;
            hr = d3dDevice->CreateTexture2D(&dtd, nullptr, &sc.depthTextures[i]);
            REQUIRE(SUCCEEDED(hr), "CreateTexture2D(depth)");
            D3D11_DEPTH_STENCIL_VIEW_DESC dsvDesc = {};
            dsvDesc.Format = DXGI_FORMAT_D32_FLOAT;
            dsvDesc.ViewDimension = D3D11_DSV_DIMENSION_TEXTURE2D;
            dsvDesc.Texture2D.MipSlice = 0;
            hr = d3dDevice->CreateDepthStencilView(sc.depthTextures[i], &dsvDesc, &sc.dsvs[i]);
            REQUIRE(SUCCEEDED(hr), "CreateDepthStencilView");
        }
        std::printf("eye %d: %ux%u, %u swapchain images (+depth)\n", eye, sc.width, sc.height, imageCount);
    }

    // --- 8b. Scene pipeline: VS/PS, input layout (POSITION + COLOR), MVP (b0) + tint (b1) cbuffers,
    //         CULL_NONE rasterizer (D3D default culls; our room is viewed from the inside), depth state.
    ID3DBlob* vsBlob = compileShader(kSceneShaderHLSL, "VSMain", "vs_5_0");
    ID3DBlob* psBlob = compileShader(kSceneShaderHLSL, "PSMain", "ps_5_0");
    ID3D11VertexShader* sceneVS = nullptr;
    ID3D11PixelShader* scenePS = nullptr;
    REQUIRE(SUCCEEDED(d3dDevice->CreateVertexShader(vsBlob->GetBufferPointer(), vsBlob->GetBufferSize(),
                                                    nullptr, &sceneVS)),
            "CreateVertexShader");
    REQUIRE(SUCCEEDED(d3dDevice->CreatePixelShader(psBlob->GetBufferPointer(), psBlob->GetBufferSize(),
                                                   nullptr, &scenePS)),
            "CreatePixelShader");

    D3D11_INPUT_ELEMENT_DESC inputDesc[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 12, D3D11_INPUT_PER_VERTEX_DATA, 0}};
    ID3D11InputLayout* sceneLayout = nullptr;
    REQUIRE(SUCCEEDED(d3dDevice->CreateInputLayout(inputDesc, 2, vsBlob->GetBufferPointer(),
                                                   vsBlob->GetBufferSize(), &sceneLayout)),
            "CreateInputLayout");
    vsBlob->Release();
    psBlob->Release();

    // CULL_NONE: the room is an inverted box viewed from inside, so we must not cull back faces.
    ID3D11RasterizerState* noCull = nullptr;
    {
        D3D11_RASTERIZER_DESC rd = {};
        rd.FillMode = D3D11_FILL_SOLID;
        rd.CullMode = D3D11_CULL_NONE;
        rd.DepthClipEnable = TRUE;
        REQUIRE(SUCCEEDED(d3dDevice->CreateRasterizerState(&rd, &noCull)), "CreateRasterizerState");
    }

    // Depth test on, write on, LESS (D3D z 0..1, near=0).
    ID3D11DepthStencilState* depthState = nullptr;
    {
        D3D11_DEPTH_STENCIL_DESC dd = {};
        dd.DepthEnable = TRUE;
        dd.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
        dd.DepthFunc = D3D11_COMPARISON_LESS;
        dd.StencilEnable = FALSE;
        REQUIRE(SUCCEEDED(d3dDevice->CreateDepthStencilState(&dd, &depthState)),
                "CreateDepthStencilState");
    }

    // MVP constant buffer (float4x4, 64 bytes) and tint constant buffer (float4, 16 bytes).
    ID3D11Buffer* mvpCB = nullptr;
    {
        D3D11_BUFFER_DESC cbDesc = {};
        cbDesc.ByteWidth = sizeof(Mat4);
        cbDesc.Usage = D3D11_USAGE_DYNAMIC;
        cbDesc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        cbDesc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        REQUIRE(SUCCEEDED(d3dDevice->CreateBuffer(&cbDesc, nullptr, &mvpCB)), "CreateBuffer(mvpCB)");
    }
    ID3D11Buffer* tintCB = nullptr;
    {
        D3D11_BUFFER_DESC cbDesc = {};
        cbDesc.ByteWidth = 16;
        cbDesc.Usage = D3D11_USAGE_DYNAMIC;
        cbDesc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        cbDesc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        REQUIRE(SUCCEEDED(d3dDevice->CreateBuffer(&cbDesc, nullptr, &tintCB)), "CreateBuffer(tintCB)");
    }

    // --- 8b-calib. Full-screen calibration-pattern pipeline (only built in --calib mode). -----------
    //   Loads a raw RGBA8 file sized exactly to eye 0's swapchain (eyeW*eyeH*4) into an immutable
    //   R8G8B8A8_UNORM texture + SRV, and compiles the SV_VertexID full-screen textured blit shaders
    //   plus a linear-clamp sampler. In the frame loop we draw this instead of the 3D scene.
    ID3D11VertexShader* calibVS = nullptr;
    ID3D11PixelShader* calibPS = nullptr;
    ID3D11Texture2D* calibTex = nullptr;
    ID3D11ShaderResourceView* calibSRV = nullptr;
    ID3D11SamplerState* calibSampler = nullptr;
    if (calibMode) {
        const uint32_t eyeW = eyes[0].width;
        const uint32_t eyeH = eyes[0].height;
        const size_t expectBytes = size_t(eyeW) * size_t(eyeH) * 4u;

        // Read the raw RGBA8 file.
        std::vector<uint8_t> pixels;
        {
            FILE* fp = std::fopen(calibPath, "rb");
            REQUIRE(fp != nullptr, "fopen(calib pattern)");
            std::fseek(fp, 0, SEEK_END);
            long fsize = std::ftell(fp);
            std::fseek(fp, 0, SEEK_SET);
            if (fsize < 0 || size_t(fsize) != expectBytes) {
                std::fclose(fp);
                std::printf("FAIL: calib pattern size mismatch\n");
                std::exit(1);
            }
            pixels.resize(expectBytes);
            size_t got = std::fread(pixels.data(), 1, expectBytes, fp);
            std::fclose(fp);
            if (got != expectBytes) {
                std::printf("FAIL: calib pattern size mismatch\n");
                std::exit(1);
            }
        }

        // Immutable RGBA8 texture + SRV.
        D3D11_TEXTURE2D_DESC td = {};
        td.Width = eyeW;
        td.Height = eyeH;
        td.MipLevels = 1;
        td.ArraySize = 1;
        td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        td.SampleDesc.Count = 1;
        td.Usage = D3D11_USAGE_IMMUTABLE;
        td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        D3D11_SUBRESOURCE_DATA sd = {};
        sd.pSysMem = pixels.data();
        sd.SysMemPitch = eyeW * 4u;
        REQUIRE(SUCCEEDED(d3dDevice->CreateTexture2D(&td, &sd, &calibTex)), "CreateTexture2D(calib)");

        D3D11_SHADER_RESOURCE_VIEW_DESC srvDesc = {};
        srvDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        srvDesc.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        srvDesc.Texture2D.MipLevels = 1;
        REQUIRE(SUCCEEDED(d3dDevice->CreateShaderResourceView(calibTex, &srvDesc, &calibSRV)),
                "CreateShaderResourceView(calib)");

        // Full-screen blit shaders (no input layout / VB).
        ID3DBlob* cvsBlob = compileShader(kCalibShaderHLSL, "VSCalib", "vs_5_0");
        ID3DBlob* cpsBlob = compileShader(kCalibShaderHLSL, "PSCalib", "ps_5_0");
        REQUIRE(SUCCEEDED(d3dDevice->CreateVertexShader(cvsBlob->GetBufferPointer(),
                                                        cvsBlob->GetBufferSize(), nullptr, &calibVS)),
                "CreateVertexShader(calib)");
        REQUIRE(SUCCEEDED(d3dDevice->CreatePixelShader(cpsBlob->GetBufferPointer(),
                                                       cpsBlob->GetBufferSize(), nullptr, &calibPS)),
                "CreatePixelShader(calib)");
        cvsBlob->Release();
        cpsBlob->Release();

        // Linear-clamp sampler.
        D3D11_SAMPLER_DESC smp = {};
        smp.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
        smp.AddressU = D3D11_TEXTURE_ADDRESS_CLAMP;
        smp.AddressV = D3D11_TEXTURE_ADDRESS_CLAMP;
        smp.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        smp.MaxLOD = D3D11_FLOAT32_MAX;
        REQUIRE(SUCCEEDED(d3dDevice->CreateSamplerState(&smp, &calibSampler)),
                "CreateSamplerState(calib)");
        std::printf("calib mode: pattern %ux%u loaded from %s\n", eyeW, eyeH, calibPath);
    }

    ID3D11VertexShader* stressVS = nullptr;
    ID3D11PixelShader* stressPS = nullptr;
    ID3D11Buffer* stressCB = nullptr;
    if (stressMode) {
        ID3DBlob* svs = compileShader(kStressShaderHLSL, "VSStress", "vs_5_0");
        ID3DBlob* sps = compileShader(kStressShaderHLSL, "PSStress", "ps_5_0");
        d3dDevice->CreateVertexShader(svs->GetBufferPointer(), svs->GetBufferSize(), nullptr, &stressVS);
        d3dDevice->CreatePixelShader(sps->GetBufferPointer(), sps->GetBufferSize(), nullptr, &stressPS);
        svs->Release();
        sps->Release();
        D3D11_BUFFER_DESC cb = {};
        cb.ByteWidth = 16;
        cb.Usage = D3D11_USAGE_DYNAMIC;
        cb.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        cb.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        d3dDevice->CreateBuffer(&cb, nullptr, &stressCB);
        std::printf("stress mode: full-frame animated noise\n");
    }

    // --- 8c. Build static scene geometry. -----------------------------------------------------------
    // (a) Floor grid lines (LINELIST) every 1m across [-10,10] on x and z, at y just above the floor
    //     to avoid z-fighting with the dark floor plane. Light cyan-gray.
    std::vector<Vertex> gridVerts;
    {
        const float gy = 0.01f;
        const float lr = 0.45f, lg = 0.65f, lb = 0.75f;
        for (int i = -10; i <= 10; ++i) {
            const float f = float(i);
            gridVerts.push_back({-10.f, gy, f, lr, lg, lb});   // line along x
            gridVerts.push_back({10.f, gy, f, lr, lg, lb});
            gridVerts.push_back({f, gy, -10.f, lr, lg, lb});   // line along z
            gridVerts.push_back({f, gy, 10.f, lr, lg, lb});
        }
    }

    // (b) Static triangle scene: dark floor plane + enclosing room (inverted 20x6x20 box centered at
    //     (0,3,0), i.e. x,z in [-10,10], y in [0,6]) with a distinct muted color per wall + ceiling.
    std::vector<Vertex> sceneVerts;
    {
        // Dark floor plane at y=0.
        const float f0[3] = {-10.f, 0.f, -10.f}, f1[3] = {10.f, 0.f, -10.f};
        const float f2[3] = {10.f, 0.f, 10.f}, f3[3] = {-10.f, 0.f, 10.f};
        pushQuad(sceneVerts, f0, f1, f2, f3, 0.12f, 0.14f, 0.16f);

        // Room walls (winding irrelevant under CULL_NONE); muted colors.
        const float x0 = -10.f, x1 = 10.f, y0 = 0.f, y1 = 6.f, z0 = -10.f, z1 = 10.f;
        // Back wall (z = z0), muted red.
        { const float a[3]={x0,y0,z0}, b[3]={x1,y0,z0}, c[3]={x1,y1,z0}, d[3]={x0,y1,z0};
          pushQuad(sceneVerts, a, b, c, d, 0.40f, 0.22f, 0.22f); }
        // Front wall (z = z1), muted green.
        { const float a[3]={x0,y0,z1}, b[3]={x1,y0,z1}, c[3]={x1,y1,z1}, d[3]={x0,y1,z1};
          pushQuad(sceneVerts, a, b, c, d, 0.22f, 0.38f, 0.24f); }
        // Left wall (x = x0), muted blue.
        { const float a[3]={x0,y0,z0}, b[3]={x0,y0,z1}, c[3]={x0,y1,z1}, d[3]={x0,y1,z0};
          pushQuad(sceneVerts, a, b, c, d, 0.22f, 0.26f, 0.42f); }
        // Right wall (x = x1), muted amber.
        { const float a[3]={x1,y0,z0}, b[3]={x1,y0,z1}, c[3]={x1,y1,z1}, d[3]={x1,y1,z0};
          pushQuad(sceneVerts, a, b, c, d, 0.42f, 0.36f, 0.20f); }
        // Ceiling (y = y1), muted slate.
        { const float a[3]={x0,y1,z0}, b[3]={x1,y1,z0}, c[3]={x1,y1,z1}, d[3]={x0,y1,z1};
          pushQuad(sceneVerts, a, b, c, d, 0.28f, 0.30f, 0.34f); }
    }

    // (c) Unit cube (spanning -0.5..0.5) with white vertex colors; instanced per cube via model + tint.
    std::vector<Vertex> cubeVerts;
    std::vector<uint16_t> cubeIdx;
    {
        const float h = 0.5f;
        const float p[8][3] = {
            {-h, -h, -h}, {h, -h, -h}, {h, h, -h}, {-h, h, -h},
            {-h, -h, h},  {h, -h, h},  {h, h, h},  {-h, h, h}};
        for (int i = 0; i < 8; ++i) cubeVerts.push_back({p[i][0], p[i][1], p[i][2], 1.f, 1.f, 1.f});
        const uint16_t faces[6][4] = {
            {0, 1, 2, 3},  // -z
            {5, 4, 7, 6},  // +z
            {4, 0, 3, 7},  // -x
            {1, 5, 6, 2},  // +x
            {3, 2, 6, 7},  // +y
            {4, 5, 1, 0}   // -y
        };
        for (auto& f : faces) {
            cubeIdx.push_back(f[0]); cubeIdx.push_back(f[1]); cubeIdx.push_back(f[2]);
            cubeIdx.push_back(f[0]); cubeIdx.push_back(f[2]); cubeIdx.push_back(f[3]);
        }
    }

    // Upload the three static buffers.
    auto makeVB = [&](const std::vector<Vertex>& v) {
        ID3D11Buffer* b = nullptr;
        D3D11_BUFFER_DESC bd = {};
        bd.ByteWidth = UINT(v.size() * sizeof(Vertex));
        bd.Usage = D3D11_USAGE_IMMUTABLE;
        bd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
        D3D11_SUBRESOURCE_DATA sd = {};
        sd.pSysMem = v.data();
        REQUIRE(SUCCEEDED(d3dDevice->CreateBuffer(&bd, &sd, &b)), "CreateBuffer(VB)");
        return b;
    };
    ID3D11Buffer* gridVB = makeVB(gridVerts);
    ID3D11Buffer* sceneVB = makeVB(sceneVerts);
    ID3D11Buffer* cubeVB = makeVB(cubeVerts);
    ID3D11Buffer* cubeIB = nullptr;
    {
        D3D11_BUFFER_DESC bd = {};
        bd.ByteWidth = UINT(cubeIdx.size() * sizeof(uint16_t));
        bd.Usage = D3D11_USAGE_IMMUTABLE;
        bd.BindFlags = D3D11_BIND_INDEX_BUFFER;
        D3D11_SUBRESOURCE_DATA sd = {};
        sd.pSysMem = cubeIdx.data();
        REQUIRE(SUCCEEDED(d3dDevice->CreateBuffer(&bd, &sd, &cubeIB)), "CreateBuffer(cubeIB)");
    }

    // The colored cubes: center position, uniform scale (0.5m), and tint.
    struct CubeInstance { float pos[3]; float scale; float tint[3]; };
    const CubeInstance cubes[] = {
        {{0.f, 0.5f, -2.f}, 0.5f, {0.90f, 0.15f, 0.15f}},   // red
        {{2.f, 0.5f, -3.f}, 0.5f, {0.15f, 0.85f, 0.20f}},   // green
        {{-2.f, 1.0f, -4.f}, 0.5f, {0.20f, 0.30f, 0.95f}},  // blue
        {{0.f, 2.0f, -5.f}, 0.5f, {0.95f, 0.85f, 0.15f}},   // yellow
    };
    const int cubeCount = int(sizeof(cubes) / sizeof(cubes[0]));

    // Helper: upload an MVP (row-major) to b0 and a tint to b1.
    auto setMVP = [&](const Mat4& mvp) {
        D3D11_MAPPED_SUBRESOURCE m = {};
        if (SUCCEEDED(d3dContext->Map(mvpCB, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
            std::memcpy(m.pData, mvp.m, sizeof(mvp.m));
            d3dContext->Unmap(mvpCB, 0);
        }
    };
    auto setTint = [&](float r, float g, float b) {
        const float t[4] = {r, g, b, 1.0f};
        D3D11_MAPPED_SUBRESOURCE m = {};
        if (SUCCEEDED(d3dContext->Map(tintCB, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
            std::memcpy(m.pData, t, sizeof(t));
            d3dContext->Unmap(tintCB, 0);
        }
    };

    // --- fps timer for the periodic progress line. --------------------------------------------------
    LARGE_INTEGER perfFreq, tPrev;
    QueryPerformanceFrequency(&perfFreq);
    QueryPerformanceCounter(&tPrev);

    // --- 9./10. Event + frame loop. ----------------------------------------------------------------
    bool running = true;
    bool sessionRunning = false;
    XrSessionState state = XR_SESSION_STATE_UNKNOWN;
    int frame = 0;
    XrVector3f lastHeadPos = {0.f, 0.f, 0.f};

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
            lastHeadPos = views[0].pose.position;

            const float clearColor[4] = {0.35f, 0.55f, 0.85f, 1.0f};   // sky blue

            for (int eye = 0; eye < 2; ++eye) {
                EyeSwapchain& sc = eyes[eye];
                uint32_t imageIndex = 0;
                XrSwapchainImageAcquireInfo acquire = {XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
                XRCHECK(xrAcquireSwapchainImage(sc.handle, &acquire, &imageIndex));
                XrSwapchainImageWaitInfo scWait = {XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
                scWait.timeout = XR_INFINITE_DURATION;
                XRCHECK(xrWaitSwapchainImage(sc.handle, &scWait));

                ID3D11RenderTargetView* rtv = sc.rtvs[imageIndex];
                ID3D11DepthStencilView* dsv = sc.dsvs[imageIndex];
                d3dContext->OMSetRenderTargets(1, &rtv, dsv);
                d3dContext->ClearRenderTargetView(rtv, clearColor);
                d3dContext->ClearDepthStencilView(dsv, D3D11_CLEAR_DEPTH, 1.0f, 0);

                D3D11_VIEWPORT vp = {};
                vp.Width = float(sc.width);
                vp.Height = float(sc.height);
                vp.MaxDepth = 1.0f;
                d3dContext->RSSetViewports(1, &vp);

                if (stressMode) {
                    D3D11_MAPPED_SUBRESOURCE m = {};
                    if (SUCCEEDED(d3dContext->Map(stressCB, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
                        unsigned seed[4] = {(unsigned)frame, 0, 0, 0};
                        std::memcpy(m.pData, seed, sizeof(seed));
                        d3dContext->Unmap(stressCB, 0);
                    }
                    d3dContext->IASetInputLayout(nullptr);
                    d3dContext->IASetVertexBuffers(0, 0, nullptr, nullptr, nullptr);
                    d3dContext->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
                    d3dContext->VSSetShader(stressVS, nullptr, 0);
                    d3dContext->PSSetShader(stressPS, nullptr, 0);
                    d3dContext->PSSetConstantBuffers(0, 1, &stressCB);
                    d3dContext->RSSetState(noCull);
                    d3dContext->OMSetDepthStencilState(depthState, 0);
                    d3dContext->Draw(3, 0);
                    XrSwapchainImageReleaseInfo release = {XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
                    XRCHECK(xrReleaseSwapchainImage(sc.handle, &release));
                    projViews[eye] = {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW};
                    projViews[eye].pose = views[eye].pose;
                    projViews[eye].fov = views[eye].fov;
                    projViews[eye].subImage.swapchain = sc.handle;
                    projViews[eye].subImage.imageRect.offset = {0, 0};
                    projViews[eye].subImage.imageRect.extent = {int32_t(sc.width), int32_t(sc.height)};
                    projViews[eye].subImage.imageArrayIndex = 0;
                    continue;
                }
                if (calibMode) {
                    // Full-screen calibration pattern (same texture in both eyes); no 3D scene.
                    d3dContext->IASetInputLayout(nullptr);
                    d3dContext->IASetVertexBuffers(0, 0, nullptr, nullptr, nullptr);
                    d3dContext->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
                    d3dContext->VSSetShader(calibVS, nullptr, 0);
                    d3dContext->PSSetShader(calibPS, nullptr, 0);
                    d3dContext->PSSetShaderResources(0, 1, &calibSRV);
                    d3dContext->PSSetSamplers(0, 1, &calibSampler);
                    d3dContext->RSSetState(noCull);
                    d3dContext->OMSetDepthStencilState(depthState, 0);
                    d3dContext->Draw(3, 0);

                    XrSwapchainImageReleaseInfo release = {XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
                    XRCHECK(xrReleaseSwapchainImage(sc.handle, &release));

                    projViews[eye] = {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW};
                    projViews[eye].pose = views[eye].pose;
                    projViews[eye].fov = views[eye].fov;
                    projViews[eye].subImage.swapchain = sc.handle;
                    projViews[eye].subImage.imageRect.offset = {0, 0};
                    projViews[eye].subImage.imageRect.extent = {int32_t(sc.width), int32_t(sc.height)};
                    projViews[eye].subImage.imageArrayIndex = 0;
                    continue;   // skip the 3D room/grid/cubes for this eye
                }

                // Common pipeline state for the scene draws.
                d3dContext->IASetInputLayout(sceneLayout);
                d3dContext->VSSetShader(sceneVS, nullptr, 0);
                d3dContext->PSSetShader(scenePS, nullptr, 0);
                d3dContext->RSSetState(noCull);
                d3dContext->OMSetDepthStencilState(depthState, 0);
                d3dContext->VSSetConstantBuffers(0, 1, &mvpCB);
                d3dContext->PSSetConstantBuffers(1, 1, &tintCB);

                // view * proj is shared by every object this eye; model varies per object.
                const Mat4 viewMat = makeView(views[eye]);
                const Mat4 projMat = makeProj(views[eye].fov, 0.05f, 100.0f);
                const Mat4 viewProj = mul(viewMat, projMat);

                const UINT stride = sizeof(Vertex), offset = 0;

                // Floor grid lines (LINELIST), model = identity, white tint (colors are per-vertex).
                setMVP(viewProj);
                setTint(1.f, 1.f, 1.f);
                d3dContext->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_LINELIST);
                d3dContext->IASetVertexBuffers(0, 1, &gridVB, &stride, &offset);
                d3dContext->Draw(UINT(gridVerts.size()), 0);

                // Static room + floor plane (TRIANGLELIST), model = identity, white tint.
                d3dContext->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
                d3dContext->IASetVertexBuffers(0, 1, &sceneVB, &stride, &offset);
                d3dContext->Draw(UINT(sceneVerts.size()), 0);

                // Solid colored cubes (TRIANGLELIST, indexed), per-cube model + tint. Each cube spins
                // and orbits/bobs around its base so the scene keeps moving on its own — realistic,
                // continuously-changing content for the encoder, and no need for the wearer to move.
                const float t = float(frame) / 72.0f;   // seconds (72 Hz frame cadence)
                d3dContext->IASetVertexBuffers(0, 1, &cubeVB, &stride, &offset);
                d3dContext->IASetIndexBuffer(cubeIB, DXGI_FORMAT_R16_UINT, 0);
                for (int c = 0; c < cubeCount; ++c) {
                    const CubeInstance& ci = cubes[c];
                    const float phase = float(c) * 1.7f;
                    const float ox = 0.6f * std::cos(t * 0.9f + phase);      // horizontal orbit
                    const float oy = 0.35f * std::sin(t * 1.6f + phase);     // vertical bob
                    const float oz = 0.6f * std::sin(t * 0.9f + phase);      // depth orbit (parallax)
                    const float spin = t * (0.8f + 0.3f * float(c));
                    const Mat4 model = makeModelSpin(ci.scale, spin,
                                                     ci.pos[0] + ox, ci.pos[1] + oy, ci.pos[2] + oz);
                    setMVP(mul(model, viewProj));
                    setTint(ci.tint[0], ci.tint[1], ci.tint[2]);
                    d3dContext->DrawIndexed(UINT(cubeIdx.size()), 0, 0);
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
            std::printf("frame %d, %.1f fps | head/eye0=(%.3f, %.3f, %.3f)\n", frame,
                        secs > 0.0 ? 72.0 / secs : 0.0, lastHeadPos.x, lastHeadPos.y, lastHeadPos.z);
        }
        if (frame >= maxFrames) running = false;   // --frames N cap (default 3600)
    }

    // --- 11. Teardown: geometry/pipeline -> swapchains (+ RTVs/depth) -> space -> session ->
    //         instance -> D3D11. -----------------------------------------------------------------
    if (calibSampler) calibSampler->Release();
    if (calibSRV) calibSRV->Release();
    if (calibTex) calibTex->Release();
    if (calibPS) calibPS->Release();
    if (calibVS) calibVS->Release();
    if (cubeIB) cubeIB->Release();
    if (cubeVB) cubeVB->Release();
    if (sceneVB) sceneVB->Release();
    if (gridVB) gridVB->Release();
    if (tintCB) tintCB->Release();
    if (mvpCB) mvpCB->Release();
    if (depthState) depthState->Release();
    if (noCull) noCull->Release();
    if (sceneLayout) sceneLayout->Release();
    if (scenePS) scenePS->Release();
    if (sceneVS) sceneVS->Release();
    for (int eye = 0; eye < 2; ++eye) {
        for (ID3D11DepthStencilView* dsv : eyes[eye].dsvs) {
            if (dsv) dsv->Release();
        }
        for (ID3D11Texture2D* dt : eyes[eye].depthTextures) {
            if (dt) dt->Release();
        }
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

    std::printf("ROOM DONE\n");
    return 0;
}
