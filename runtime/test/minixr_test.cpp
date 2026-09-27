// minixr_test — a stand-in for the OpenXR loader, so we can validate our runtime's negotiation and
// dispatch without needing the official loader or a full app (that comes in M1). It performs the
// documented negotiation handshake, then drives a few entry points through the returned
// xrGetInstanceProcAddr and prints what came back.
#include <windows.h>

#include <cstdio>
#include <cstring>

#include <openxr/openxr.h>
#include <openxr/openxr_loader_negotiation.h>

#define CHECK(cond, msg) do { if (!(cond)) { std::printf("FAIL: %s\n", msg); return 1; } } while (0)

int main(int argc, char** argv) {
    const char* dll = argc > 1 ? argv[1] : "xrwired_runtime.dll";
    HMODULE lib = LoadLibraryA(dll);
    CHECK(lib != nullptr, "LoadLibrary(runtime dll)");

    auto negotiate = reinterpret_cast<PFN_xrNegotiateLoaderRuntimeInterface>(
        GetProcAddress(lib, "xrNegotiateLoaderRuntimeInterface"));
    CHECK(negotiate != nullptr, "GetProcAddress(xrNegotiateLoaderRuntimeInterface)");

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

    CHECK(negotiate(&loaderInfo, &req) == XR_SUCCESS, "negotiate");
    CHECK(req.getInstanceProcAddr != nullptr, "getInstanceProcAddr returned");
    std::printf("negotiated: runtimeInterfaceVersion=%u apiVersion=%llu.%llu\n",
                req.runtimeInterfaceVersion,
                (unsigned long long)XR_VERSION_MAJOR(req.runtimeApiVersion),
                (unsigned long long)XR_VERSION_MINOR(req.runtimeApiVersion));
    PFN_xrGetInstanceProcAddr gipa = req.getInstanceProcAddr;

    auto get = [&](const char* name) -> PFN_xrVoidFunction {
        PFN_xrVoidFunction fn = nullptr;
        gipa(XR_NULL_HANDLE, name, &fn);
        return fn;
    };

    // Extensions
    auto enumExt = reinterpret_cast<PFN_xrEnumerateInstanceExtensionProperties>(
        get("xrEnumerateInstanceExtensionProperties"));
    CHECK(enumExt != nullptr, "gipa(xrEnumerateInstanceExtensionProperties)");
    uint32_t extCount = 0;
    enumExt(nullptr, 0, &extCount, nullptr);
    XrExtensionProperties exts[8] = {};
    for (auto& e : exts) e.type = XR_TYPE_EXTENSION_PROPERTIES;
    enumExt(nullptr, extCount, &extCount, exts);
    std::printf("extensions (%u):", extCount);
    for (uint32_t i = 0; i < extCount; ++i) std::printf(" %s", exts[i].extensionName);
    std::printf("\n");

    // Instance
    auto createInstance = reinterpret_cast<PFN_xrCreateInstance>(get("xrCreateInstance"));
    CHECK(createInstance != nullptr, "gipa(xrCreateInstance)");
    XrInstanceCreateInfo ici = {XR_TYPE_INSTANCE_CREATE_INFO};
    const char* wanted[] = {"XR_KHR_D3D11_enable"};
    ici.enabledExtensionCount = 1;
    ici.enabledExtensionNames = wanted;
    std::strncpy(ici.applicationInfo.applicationName, "minixr_test", XR_MAX_APPLICATION_NAME_SIZE - 1);
    ici.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
    XrInstance instance = XR_NULL_HANDLE;
    CHECK(createInstance(&ici, &instance) == XR_SUCCESS && instance != XR_NULL_HANDLE, "xrCreateInstance");

    // System
    auto getSystem = reinterpret_cast<PFN_xrGetSystem>(get("xrGetSystem"));
    CHECK(getSystem != nullptr, "gipa(xrGetSystem)");
    XrSystemGetInfo sgi = {XR_TYPE_SYSTEM_GET_INFO};
    sgi.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    XrSystemId system = XR_NULL_SYSTEM_ID;
    CHECK(getSystem(instance, &sgi, &system) == XR_SUCCESS && system != XR_NULL_SYSTEM_ID, "xrGetSystem");

    // View config
    auto enumViews = reinterpret_cast<PFN_xrEnumerateViewConfigurationViews>(
        get("xrEnumerateViewConfigurationViews"));
    CHECK(enumViews != nullptr, "gipa(xrEnumerateViewConfigurationViews)");
    uint32_t viewCount = 0;
    enumViews(instance, system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, 0, &viewCount, nullptr);
    XrViewConfigurationView views[2] = {};
    for (auto& v : views) v.type = XR_TYPE_VIEW_CONFIGURATION_VIEW;
    enumViews(instance, system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, viewCount, &viewCount, views);
    std::printf("stereo views=%u, per-eye %ux%u\n", viewCount,
                views[0].recommendedImageRectWidth, views[0].recommendedImageRectHeight);

    std::printf("PASS\n");
    return 0;
}
