// xrw_probe - a full D3D11 OpenXR app that drives OUR runtime end to end: it negotiates the runtime
// interface directly (no official loader), creates a session against a real ID3D11Device, and runs a
// frame loop that renders a moving test pattern into the runtime's swapchains each frame.
//
// It is the D3D11/Windows sibling of receiver-xr's xr_core.cpp (the Android/GLES client of the same
// calls). Every OpenXR function is fetched through the runtime's xrGetInstanceProcAddr, exactly like
// minixr_test.cpp's negotiation handshake - so this exe never links the Khronos loader.
//
// Success bar for now is a clean compile with the correct call sequence; it is meant to be run later,
// once the runtime's M1 frame loop lands.
#define _CRT_SECURE_NO_WARNINGS   // strncpy into the fixed-size OpenXR name fields is intentional
#include <d3d11.h>          // must precede openxr_platform.h so ID3D11Device/Texture2D are defined
#include <d3d11_1.h>        // ID3D11DeviceContext1::ClearView, for the scissored bar
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
    std::strncpy(ici.applicationInfo.applicationName, "xrw_probe", XR_MAX_APPLICATION_NAME_SIZE - 1);
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

            // Animated color: a hue that scrolls each frame, so a static capture still shows motion.
            float phase = float(frame) * 0.02f;
            float clearColor[4] = {0.5f + 0.5f * std::sin(phase),
                                   0.5f + 0.5f * std::sin(phase + 2.094f),
                                   0.5f + 0.5f * std::sin(phase + 4.188f), 1.0f};
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
                d3dContext->ClearRenderTargetView(rtv, clearColor);

                // A vertical bar that scans left-to-right, drawn as a scissored clear of white.
                D3D11_VIEWPORT vp = {};
                vp.Width = float(sc.width);
                vp.Height = float(sc.height);
                vp.MaxDepth = 1.0f;
                d3dContext->RSSetViewports(1, &vp);
                uint32_t barW = sc.width / 20;
                uint32_t barX = (frame * (sc.width / 120 + 1)) % (sc.width - barW);
                D3D11_RECT scissor = {LONG(barX), 0, LONG(barX + barW), LONG(sc.height)};
                d3dContext->RSSetScissorRects(1, &scissor);
                // The bar is a white ClearView bounded by the scissor rect: ClearRenderTargetView
                // ignores scissor, but ID3D11DeviceContext1::ClearView (11.1) clears only the given
                // rect - no shaders needed for an obvious moving marker.
                float barColor[4] = {1.0f, 1.0f, 1.0f, 1.0f};
                ID3D11DeviceContext1* ctx1 = nullptr;
                if (SUCCEEDED(d3dContext->QueryInterface(__uuidof(ID3D11DeviceContext1),
                                                         reinterpret_cast<void**>(&ctx1))) &&
                    ctx1 != nullptr) {
                    ctx1->ClearView(rtv, barColor, &scissor, 1);   // 11.1: clear only the scissor rect
                    ctx1->Release();
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
            std::printf("frame %d, %.1f fps\n", frame, secs > 0.0 ? 72.0 / secs : 0.0);
        }
        if (frame >= maxFrames) running = false;   // --frames N cap (default 600)
    }

    // --- 11. Teardown: swapchains (+ RTVs) -> space -> session -> instance -> D3D11. ----------------
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

    std::printf("PROBE DONE\n");
    return 0;
}
