// xr_core: the OpenXR half of the VR receiver. Owns the EGL context, the XR session and the two eye
// swapchains, and runs the frame loop that draws each decoded frame as a projection layer - one view
// per eye, with the runtime's own reprojection - instead of a flat panel.
//
// Threads: Java calls start() on a dedicated thread and that thread owns GL and XR for its lifetime.
// The network thread asks for the decoder surface through create_decoder_surface(); the request is
// handed to the XR thread (a SurfaceTexture must be created where the GL context is current) and the
// caller waits for it.
//
// Timing: every submitted frame records (access unit pts, predicted display time, submit time), which
// Java drains and sends back to the PC. That is a real VR display timestamp, not a flat compositor
// latch, so the sender can measure true motion-to-photon minus the PC's own share.
#define XR_USE_PLATFORM_ANDROID
#define XR_USE_GRAPHICS_API_OPENGL_ES
#define XR_USE_TIMESPEC                  // for XR_KHR_convert_timespec_time

#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <android/log.h>
#include <jni.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>

#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include "stereo_renderer.h"

#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "XRWiredXR", __VA_ARGS__)
#define LOGW(...) __android_log_print(ANDROID_LOG_WARN, "XRWiredXR", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "XRWiredXR", __VA_ARGS__)

namespace {

struct EyeSwapchain {
    XrSwapchain handle = XR_NULL_HANDLE;
    int32_t width = 0, height = 0;
    std::vector<GLuint> images;
    std::vector<GLuint> framebuffers;
};

// What the PC needs to render the next frame: where the head will be when the frame is shown, and
// the projection it should use. Each pose carries an id; the driver stamps the frame it renders for
// that pose with the same id, so the client can reproject against the pose the image was made for.
struct HeadPose {
    uint64_t id = 0;
    float orientation[4] = {0, 0, 0, 1};    // x, y, z, w
    float position[3] = {0, 0, 0};
    float fov[8] = {};                      // left eye angles l,r,u,d then right eye
    float linear_velocity[3] = {};          // m/s and rad/s: without these SteamVR cannot extrapolate
    float angular_velocity[3] = {};         //   between our pose samples, and the view stutters
};

// The pose we asked the PC to render for, kept until the matching frame comes back. Showing a frame
// at the pose it was rendered for is what makes the world stay put: the runtime then only has to
// correct the small delta to the real display pose (its own reprojection), instead of us pinning a
// stale image to the current head pose.
struct RenderedPose {
    XrPosef views[2];
    XrFovf fov[2];
};

struct DisplayEvent {
    int64_t pts_us;               // access unit the frame came from (SurfaceTexture timestamp)
    int64_t predicted_display_ns; // when the runtime will show it
    int64_t submitted_ns;         // when we called xrEndFrame
};

// Latest hand joints, forwarded to the PC so the runtime can serve XR_EXT_hand_tracking. 26 joints
// per hand, each 8 floats: px,py,pz, qx,qy,qz,qw, radius (XrHandJointEXT order 0..25).
constexpr int kJoints = 26;
struct Hands {
    std::mutex mutex;
    bool active[2] = {false, false};
    float joint[2][kJoints * 8] = {};
};

struct Xr {
    JavaVM* vm = nullptr;
    jobject activity = nullptr;

    EGLDisplay egl_display = EGL_NO_DISPLAY;
    EGLContext egl_context = EGL_NO_CONTEXT;
    EGLSurface egl_surface = EGL_NO_SURFACE;
    EGLConfig egl_config = nullptr;

    XrInstance instance = XR_NULL_HANDLE;
    XrSystemId system = XR_NULL_SYSTEM_ID;
    XrSession session = XR_NULL_HANDLE;
    XrSpace space = XR_NULL_HANDLE;
    XrSpace view_space = XR_NULL_HANDLE;
    XrSessionState state = XR_SESSION_STATE_UNKNOWN;
    EyeSwapchain eyes[2];
    bool session_running = false;
    bool ten_bit_swapchain = false;

    PFN_xrConvertTimeToTimespecTimeKHR convert_time = nullptr;
    bool has_refresh_rate = false;
    bool has_local_floor = false;
    bool has_hand_tracking = false;
    PFN_xrCreateHandTrackerEXT create_hand_tracker = nullptr;
    PFN_xrDestroyHandTrackerEXT destroy_hand_tracker = nullptr;
    PFN_xrLocateHandJointsEXT locate_hand_joints = nullptr;
    XrHandTrackerEXT hand_tracker[2] = {XR_NULL_HANDLE, XR_NULL_HANDLE};
    Hands hands;
    std::atomic<int> submit_margin_ms{0};   // 0 = submit as soon as the frame is rendered
    StereoRenderer renderer;
    std::atomic<bool> running{false};
    std::atomic<bool> want_ten_bit{false};
    std::atomic<bool> probe_pixels{false};     // log XRPIX samples of what we are about to display
    std::atomic<int> probe_count{0};           // capture a few times then stop (glReadPixels stalls)
    uint64_t displayed_pose_id = 0;            // pose id of the frame currently on the texture     // set before start() by NativeXr.setTenBit()
    std::atomic<int64_t> submitted{0}, dropped_no_frame{0}, late_submits{0}, stale_frames{0};

    std::mutex pose_mutex;
    HeadPose pose;
    std::map<uint64_t, RenderedPose> pose_history;    // pose id -> what we asked the PC to render
    std::mutex mutex;                     // guards the surface request and the event queue
    std::condition_variable surface_ready;
    bool surface_requested = false;
    int surface_width = 0, surface_height = 0;
    bool surface_ten_bit = false;
    jobject surface_result = nullptr;
    std::vector<DisplayEvent> events;
};

Xr g;

const char* xr_error(XrResult result) {
    static char text[XR_MAX_RESULT_STRING_SIZE];
    if (g.instance != XR_NULL_HANDLE && xrResultToString(g.instance, result, text) == XR_SUCCESS) return text;
    std::snprintf(text, sizeof(text), "XrResult %d", result);
    return text;
}

bool check(XrResult result, const char* what) {
    if (XR_SUCCEEDED(result)) return true;
    LOGE("%s failed: %s", what, xr_error(result));
    return false;
}

// XrTime on the runtime's own scale -> CLOCK_MONOTONIC nanoseconds (what System.nanoTime() reports).
int64_t xr_time_to_monotonic_ns(XrTime time) {
    if (g.convert_time == nullptr) return int64_t(time);     // Android runtimes already use this clock
    timespec ts{};
    if (XR_FAILED(g.convert_time(g.instance, time, &ts))) return int64_t(time);
    return int64_t(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

int64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return int64_t(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

bool init_egl() {
    g.egl_display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (g.egl_display == EGL_NO_DISPLAY || !eglInitialize(g.egl_display, nullptr, nullptr)) {
        LOGE("eglInitialize failed");
        return false;
    }
    const EGLint config_attrs[] = {EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
                                   EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
                                   EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
                                   EGL_NONE};
    EGLint count = 0;
    if (!eglChooseConfig(g.egl_display, config_attrs, &g.egl_config, 1, &count) || count == 0) {
        LOGE("eglChooseConfig failed");
        return false;
    }
    const EGLint context_attrs[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    g.egl_context = eglCreateContext(g.egl_display, g.egl_config, EGL_NO_CONTEXT, context_attrs);
    const EGLint pbuffer_attrs[] = {EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE};
    g.egl_surface = eglCreatePbufferSurface(g.egl_display, g.egl_config, pbuffer_attrs);
    if (g.egl_context == EGL_NO_CONTEXT || g.egl_surface == EGL_NO_SURFACE ||
        !eglMakeCurrent(g.egl_display, g.egl_surface, g.egl_surface, g.egl_context)) {
        LOGE("EGL context creation failed");
        return false;
    }
    LOGI("EGL ready: %s", glGetString(GL_VERSION));
    return true;
}

// The Khronos Android loader needs the VM and activity before any other OpenXR call.
bool init_loader(JNIEnv* env) {
    PFN_xrInitializeLoaderKHR initialize = nullptr;
    if (XR_FAILED(xrGetInstanceProcAddr(XR_NULL_HANDLE, "xrInitializeLoaderKHR",
                                        reinterpret_cast<PFN_xrVoidFunction*>(&initialize))) ||
        initialize == nullptr) {
        LOGW("xrInitializeLoaderKHR unavailable; continuing");
        return true;
    }
    XrLoaderInitInfoAndroidKHR info{XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};
    info.applicationVM = g.vm;
    info.applicationContext = g.activity;
    return check(initialize(reinterpret_cast<const XrLoaderInitInfoBaseHeaderKHR*>(&info)), "xrInitializeLoaderKHR");
}

bool has_extension(const std::vector<XrExtensionProperties>& list, const char* name) {
    for (const auto& e : list) {
        if (std::strcmp(e.extensionName, name) == 0) return true;
    }
    return false;
}

bool init_instance() {
    uint32_t count = 0;
    xrEnumerateInstanceExtensionProperties(nullptr, 0, &count, nullptr);
    std::vector<XrExtensionProperties> extensions(count, {XR_TYPE_EXTENSION_PROPERTIES});
    xrEnumerateInstanceExtensionProperties(nullptr, count, &count, extensions.data());
    std::string names;                       // a few per line: one long line gets truncated by logcat
    for (uint32_t i = 0; i < count; ++i) {
        names += std::string(extensions[i].extensionName) + " ";
        if (i % 6 == 5 || i + 1 == count) {
            LOGI("XREXT %s", names.c_str());
            names.clear();
        }
    }
    LOGI("XREXT count=%u", count);

    if (!has_extension(extensions, XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME)) {
        LOGE("runtime has no XR_KHR_opengl_es_enable");
        return false;
    }
    std::vector<const char*> enabled{XR_KHR_ANDROID_CREATE_INSTANCE_EXTENSION_NAME,
                                     XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME};
    // Lets us state display times on Android's own clock, so Java can compare them with System.nanoTime().
    // A floor-relative origin, so the PC places the head at the right height above SteamVR's floor.
    g.has_local_floor = has_extension(extensions, XR_EXT_LOCAL_FLOOR_EXTENSION_NAME);
    if (g.has_local_floor) enabled.push_back(XR_EXT_LOCAL_FLOOR_EXTENSION_NAME);
    const bool has_timespec = has_extension(extensions, XR_KHR_CONVERT_TIMESPEC_TIME_EXTENSION_NAME);
    if (has_timespec) enabled.push_back(XR_KHR_CONVERT_TIMESPEC_TIME_EXTENSION_NAME);
    g.has_refresh_rate = has_extension(extensions, XR_FB_DISPLAY_REFRESH_RATE_EXTENSION_NAME);
    if (g.has_refresh_rate) enabled.push_back(XR_FB_DISPLAY_REFRESH_RATE_EXTENSION_NAME);
    g.has_hand_tracking = has_extension(extensions, XR_EXT_HAND_TRACKING_EXTENSION_NAME);
    if (g.has_hand_tracking) enabled.push_back(XR_EXT_HAND_TRACKING_EXTENSION_NAME);

    XrInstanceCreateInfoAndroidKHR android{XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR};
    android.applicationVM = g.vm;
    android.applicationActivity = g.activity;
    XrInstanceCreateInfo info{XR_TYPE_INSTANCE_CREATE_INFO};
    info.next = &android;
    std::strncpy(info.applicationInfo.applicationName, "XR Wired receiver", XR_MAX_APPLICATION_NAME_SIZE - 1);
    std::strncpy(info.applicationInfo.engineName, "xrwired", XR_MAX_ENGINE_NAME_SIZE - 1);
    info.applicationInfo.applicationVersion = 1;
    info.applicationInfo.engineVersion = 1;
    info.applicationInfo.apiVersion = XR_CURRENT_API_VERSION;
    info.enabledExtensionCount = uint32_t(enabled.size());
    info.enabledExtensionNames = enabled.data();
    if (!check(xrCreateInstance(&info, &g.instance), "xrCreateInstance")) return false;

    if (has_timespec) {
        xrGetInstanceProcAddr(g.instance, "xrConvertTimeToTimespecTimeKHR",
                              reinterpret_cast<PFN_xrVoidFunction*>(&g.convert_time));
    }
    XrSystemGetInfo system_info{XR_TYPE_SYSTEM_GET_INFO};
    system_info.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    return check(xrGetSystem(g.instance, &system_info, &g.system), "xrGetSystem");
}

bool init_session() {
    PFN_xrGetOpenGLESGraphicsRequirementsKHR requirements_fn = nullptr;
    xrGetInstanceProcAddr(g.instance, "xrGetOpenGLESGraphicsRequirementsKHR",
                          reinterpret_cast<PFN_xrVoidFunction*>(&requirements_fn));
    if (requirements_fn != nullptr) {                 // required before xrCreateSession
        XrGraphicsRequirementsOpenGLESKHR requirements{XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_ES_KHR};
        requirements_fn(g.instance, g.system, &requirements);
    }
    XrGraphicsBindingOpenGLESAndroidKHR binding{XR_TYPE_GRAPHICS_BINDING_OPENGL_ES_ANDROID_KHR};
    binding.display = g.egl_display;
    binding.config = g.egl_config;
    binding.context = g.egl_context;
    XrSessionCreateInfo info{XR_TYPE_SESSION_CREATE_INFO};
    info.next = &binding;
    info.systemId = g.system;
    if (!check(xrCreateSession(g.instance, &info, &g.session), "xrCreateSession")) return false;

    XrReferenceSpaceCreateInfo space_info{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    space_info.referenceSpaceType = g.has_local_floor ? XR_REFERENCE_SPACE_TYPE_LOCAL_FLOOR_EXT
                                                      : XR_REFERENCE_SPACE_TYPE_LOCAL;
    space_info.poseInReferenceSpace.orientation.w = 1.0f;
    LOGI("reference space: %s", g.has_local_floor ? "local floor" : "local");
    if (!check(xrCreateReferenceSpace(g.session, &space_info, &g.space), "xrCreateReferenceSpace")) return false;
    XrReferenceSpaceCreateInfo view_info{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    view_info.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;     // to read head velocity
    view_info.poseInReferenceSpace.orientation.w = 1.0f;
    if (!check(xrCreateReferenceSpace(g.session, &view_info, &g.view_space), "view space")) return false;

    if (g.has_hand_tracking) {                        // XR_EXT_hand_tracking: forward joints to the PC
        xrGetInstanceProcAddr(g.instance, "xrCreateHandTrackerEXT",
                              reinterpret_cast<PFN_xrVoidFunction*>(&g.create_hand_tracker));
        xrGetInstanceProcAddr(g.instance, "xrDestroyHandTrackerEXT",
                              reinterpret_cast<PFN_xrVoidFunction*>(&g.destroy_hand_tracker));
        xrGetInstanceProcAddr(g.instance, "xrLocateHandJointsEXT",
                              reinterpret_cast<PFN_xrVoidFunction*>(&g.locate_hand_joints));
        for (int h = 0; h < 2 && g.create_hand_tracker; ++h) {
            XrHandTrackerCreateInfoEXT ci{XR_TYPE_HAND_TRACKER_CREATE_INFO_EXT};
            ci.hand = h == 0 ? XR_HAND_LEFT_EXT : XR_HAND_RIGHT_EXT;
            ci.handJointSet = XR_HAND_JOINT_SET_DEFAULT_EXT;
            if (!check(g.create_hand_tracker(g.session, &ci, &g.hand_tracker[h]), "xrCreateHandTrackerEXT"))
                g.hand_tracker[h] = XR_NULL_HANDLE;
        }
        LOGI("hand tracking %s", g.hand_tracker[0] != XR_NULL_HANDLE ? "on" : "unavailable");
    }
    return true;
}

int64_t pick_swapchain_format(bool ten_bit) {
    uint32_t count = 0;
    xrEnumerateSwapchainFormats(g.session, 0, &count, nullptr);
    std::vector<int64_t> formats(count);
    xrEnumerateSwapchainFormats(g.session, count, &count, formats.data());
    auto supports = [&](int64_t format) {
        for (int64_t f : formats) {
            if (f == format) return true;
        }
        return false;
    };
    // sRGB by default: the PC renders and encodes sRGB-encoded pixels, and a linear-treated swapchain
    // would show them washed out. 10-bit (linear) is opt-in, for banding and HDR experiments.
    if (ten_bit && supports(GL_RGB10_A2)) return GL_RGB10_A2;
    if (supports(GL_SRGB8_ALPHA8)) return GL_SRGB8_ALPHA8;
    if (supports(GL_RGBA8)) return GL_RGBA8;
    return formats.empty() ? GL_RGBA8 : formats[0];
}

bool create_swapchains(bool ten_bit) {
    uint32_t count = 0;
    xrEnumerateViewConfigurationViews(g.instance, g.system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, 0,
                                      &count, nullptr);
    std::vector<XrViewConfigurationView> views(count, {XR_TYPE_VIEW_CONFIGURATION_VIEW});
    xrEnumerateViewConfigurationViews(g.instance, g.system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, count,
                                      &count, views.data());
    if (count < 2) {
        LOGE("expected a stereo view configuration, got %u views", count);
        return false;
    }
    const int64_t format = pick_swapchain_format(ten_bit);
    g.ten_bit_swapchain = format == GL_RGB10_A2;
    LOGI("XRVIEW recommended %ux%u per eye, swapchain format 0x%llx (%s)", views[0].recommendedImageRectWidth,
         views[0].recommendedImageRectHeight, static_cast<unsigned long long>(format),
         g.ten_bit_swapchain ? "10-bit" : "8-bit");

    for (int eye = 0; eye < 2; ++eye) {
        EyeSwapchain& sc = g.eyes[eye];
        sc.width = int32_t(views[eye].recommendedImageRectWidth);
        sc.height = int32_t(views[eye].recommendedImageRectHeight);
        XrSwapchainCreateInfo info{XR_TYPE_SWAPCHAIN_CREATE_INFO};
        info.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_SAMPLED_BIT;
        info.format = format;
        info.sampleCount = 1;
        info.width = uint32_t(sc.width);
        info.height = uint32_t(sc.height);
        info.faceCount = 1;
        info.arraySize = 1;
        info.mipCount = 1;
        if (!check(xrCreateSwapchain(g.session, &info, &sc.handle), "xrCreateSwapchain")) return false;

        uint32_t images = 0;
        xrEnumerateSwapchainImages(sc.handle, 0, &images, nullptr);
        std::vector<XrSwapchainImageOpenGLESKHR> gl_images(images, {XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_ES_KHR});
        if (!check(xrEnumerateSwapchainImages(sc.handle, images, &images,
                                              reinterpret_cast<XrSwapchainImageBaseHeader*>(gl_images.data())),
                   "xrEnumerateSwapchainImages")) {
            return false;
        }
        sc.images.resize(images);
        sc.framebuffers.resize(images);
        glGenFramebuffers(GLsizei(images), sc.framebuffers.data());
        for (uint32_t i = 0; i < images; ++i) {
            sc.images[i] = gl_images[i].image;
            glBindFramebuffer(GL_FRAMEBUFFER, sc.framebuffers[i]);
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, sc.images[i], 0);
        }
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
    }
    return true;
}

void handle_events() {
    XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
    while (xrPollEvent(g.instance, &event) == XR_SUCCESS) {
        if (event.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
            g.state = reinterpret_cast<XrEventDataSessionStateChanged*>(&event)->state;
            LOGI("session state -> %d", int(g.state));
            if (g.state == XR_SESSION_STATE_READY) {
                XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO};
                begin.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                g.session_running = check(xrBeginSession(g.session, &begin), "xrBeginSession");
            } else if (g.state == XR_SESSION_STATE_STOPPING) {
                xrEndSession(g.session);
                g.session_running = false;
            } else if (g.state == XR_SESSION_STATE_EXITING || g.state == XR_SESSION_STATE_LOSS_PENDING) {
                g.running = false;
            }
        }
        event = {XR_TYPE_EVENT_DATA_BUFFER};
    }
}

// A SurfaceTexture must be created where the GL context is current, so the XR thread serves the
// network thread's request between frames.
void serve_surface_request(JNIEnv* env) {
    std::unique_lock<std::mutex> lock(g.mutex);
    if (!g.surface_requested) return;
    int width = g.surface_width, height = g.surface_height;
    bool ten_bit = g.surface_ten_bit;
    lock.unlock();
    jobject surface = g.renderer.create_decoder_surface(env, width, height);
    lock.lock();
    g.surface_result = surface;
    g.surface_requested = false;
    if (ten_bit && !g.ten_bit_swapchain) LOGW("10-bit stream but the swapchain is 8-bit");
    g.surface_ready.notify_all();
}

void render_frame(JNIEnv* env) {
    XrFrameWaitInfo wait_info{XR_TYPE_FRAME_WAIT_INFO};
    XrFrameState frame_state{XR_TYPE_FRAME_STATE};
    if (!check(xrWaitFrame(g.session, &wait_info, &frame_state), "xrWaitFrame")) return;
    XrFrameBeginInfo begin_info{XR_TYPE_FRAME_BEGIN_INFO};
    xrBeginFrame(g.session, &begin_info);

    const int margin_ms = g.submit_margin_ms.load();
    if (margin_ms > 0) {          // late-latch: wait, then take the newest decoded frame
        const int64_t deadline = xr_time_to_monotonic_ns(frame_state.predictedDisplayTime) -
                                 int64_t(margin_ms) * 1000000LL;
        for (int64_t remaining = deadline - now_ns(); remaining > 200000; remaining = deadline - now_ns()) {
            usleep(useconds_t(std::min<int64_t>(remaining - 200000, 2000) / 1000));
        }
    }
    int64_t frame_pts_ns = -1;
    const bool new_frame = g.renderer.update_texture(env, &frame_pts_ns);
    if (!new_frame && !g.renderer.has_frame()) g.dropped_no_frame++;

    XrCompositionLayerProjectionView projection_views[2]{};
    bool rendered = false;
    if (new_frame && frame_pts_ns > 0) g.displayed_pose_id = uint64_t(frame_pts_ns / 1000);
    if (frame_state.shouldRender == XR_TRUE) {
        XrViewState view_state{XR_TYPE_VIEW_STATE};
        uint32_t view_count = 0;
        XrView views[2] = {{XR_TYPE_VIEW}, {XR_TYPE_VIEW}};
        XrViewLocateInfo locate{XR_TYPE_VIEW_LOCATE_INFO};
        locate.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
        locate.displayTime = frame_state.predictedDisplayTime;
        locate.space = g.space;
        if (XR_SUCCEEDED(xrLocateViews(g.session, &locate, &view_state, 2, &view_count, views)) &&
            (view_state.viewStateFlags & XR_VIEW_STATE_POSITION_VALID_BIT) != 0) {
            // Show the frame at the pose the PC rendered it for; the runtime reprojects the rest.
            RenderedPose frame_pose;
            for (int eye = 0; eye < 2; ++eye) {
                frame_pose.views[eye] = views[eye].pose;
                frame_pose.fov[eye] = views[eye].fov;
            }
            {
                std::lock_guard<std::mutex> lock(g.pose_mutex);
                auto found = g.pose_history.find(g.displayed_pose_id);
                if (found != g.pose_history.end()) frame_pose = found->second;
            }
            {                                        // publish the pose this frame was located at
                std::lock_guard<std::mutex> lock(g.pose_mutex);
                g.pose.id++;
                RenderedPose rendered;
                for (int eye = 0; eye < 2; ++eye) {
                    rendered.views[eye] = views[eye].pose;
                    rendered.fov[eye] = views[eye].fov;
                }
                g.pose_history[g.pose.id] = rendered;
                while (g.pose_history.size() > 256) g.pose_history.erase(g.pose_history.begin());
                const XrPosef& head = views[0].pose;   // both eyes share the head pose plus an offset
                g.pose.orientation[0] = head.orientation.x;
                g.pose.orientation[1] = head.orientation.y;
                g.pose.orientation[2] = head.orientation.z;
                g.pose.orientation[3] = head.orientation.w;
                g.pose.position[0] = head.position.x;
                g.pose.position[1] = head.position.y;
                g.pose.position[2] = head.position.z;
                XrSpaceVelocity velocity{XR_TYPE_SPACE_VELOCITY};
                XrSpaceLocation location{XR_TYPE_SPACE_LOCATION, &velocity};
                if (XR_SUCCEEDED(xrLocateSpace(g.view_space, g.space, frame_state.predictedDisplayTime,
                                               &location))) {
                    const bool linear = (velocity.velocityFlags & XR_SPACE_VELOCITY_LINEAR_VALID_BIT) != 0;
                    const bool angular = (velocity.velocityFlags & XR_SPACE_VELOCITY_ANGULAR_VALID_BIT) != 0;
                    g.pose.linear_velocity[0] = linear ? velocity.linearVelocity.x : 0.0f;
                    g.pose.linear_velocity[1] = linear ? velocity.linearVelocity.y : 0.0f;
                    g.pose.linear_velocity[2] = linear ? velocity.linearVelocity.z : 0.0f;
                    g.pose.angular_velocity[0] = angular ? velocity.angularVelocity.x : 0.0f;
                    g.pose.angular_velocity[1] = angular ? velocity.angularVelocity.y : 0.0f;
                    g.pose.angular_velocity[2] = angular ? velocity.angularVelocity.z : 0.0f;
                }
                if (g.locate_hand_joints) {
                    std::lock_guard<std::mutex> hlock(g.hands.mutex);
                    for (int h = 0; h < 2; ++h) {
                        g.hands.active[h] = false;
                        if (g.hand_tracker[h] == XR_NULL_HANDLE) continue;
                        XrHandJointLocationEXT locs[kJoints] = {};
                        XrHandJointLocationsEXT out{XR_TYPE_HAND_JOINT_LOCATIONS_EXT};
                        out.jointCount = kJoints;
                        out.jointLocations = locs;
                        XrHandJointsLocateInfoEXT li{XR_TYPE_HAND_JOINTS_LOCATE_INFO_EXT};
                        li.baseSpace = g.space;
                        li.time = frame_state.predictedDisplayTime;
                        if (XR_SUCCEEDED(g.locate_hand_joints(g.hand_tracker[h], &li, &out)) && out.isActive) {
                            g.hands.active[h] = true;
                            for (int j = 0; j < kJoints; ++j) {
                                float* d = g.hands.joint[h] + j * 8;
                                d[0] = locs[j].pose.position.x;
                                d[1] = locs[j].pose.position.y;
                                d[2] = locs[j].pose.position.z;
                                d[3] = locs[j].pose.orientation.x;
                                d[4] = locs[j].pose.orientation.y;
                                d[5] = locs[j].pose.orientation.z;
                                d[6] = locs[j].pose.orientation.w;
                                d[7] = locs[j].radius;
                            }
                        }
                    }
                }
                for (int eye = 0; eye < 2; ++eye) {
                    g.pose.fov[eye * 4 + 0] = views[eye].fov.angleLeft;
                    g.pose.fov[eye * 4 + 1] = views[eye].fov.angleRight;
                    g.pose.fov[eye * 4 + 2] = views[eye].fov.angleUp;
                    g.pose.fov[eye * 4 + 3] = views[eye].fov.angleDown;
                }
            }
            for (int eye = 0; eye < 2; ++eye) {
                EyeSwapchain& sc = g.eyes[eye];
                uint32_t index = 0;
                XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
                if (!check(xrAcquireSwapchainImage(sc.handle, &acquire, &index), "xrAcquireSwapchainImage")) return;
                XrSwapchainImageWaitInfo wait{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
                wait.timeout = XR_INFINITE_DURATION;
                xrWaitSwapchainImage(sc.handle, &wait);
                g.renderer.render_eye(eye, sc.framebuffers[index], sc.width, sc.height);
                // One capture after the stream settles, then stop: glReadPixels stalls the render
                // thread, so continuous probing would drop the display below the stream rate.
                if (eye == 0 && g.probe_pixels && g.renderer.frames() > 90 && g.probe_count.load() < 1 &&
                    g.submitted % 30 == 0) {
                    g.renderer.probe_rows(sc.framebuffers[index], sc.width, sc.height, g.ten_bit_swapchain);
                    g.probe_count.fetch_add(1);
                }
                XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
                xrReleaseSwapchainImage(sc.handle, &release);

                projection_views[eye] = {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW};
                projection_views[eye].pose = frame_pose.views[eye];
                projection_views[eye].fov = frame_pose.fov[eye];
                projection_views[eye].subImage.swapchain = sc.handle;
                projection_views[eye].subImage.imageRect.offset = {0, 0};
                projection_views[eye].subImage.imageRect.extent = {sc.width, sc.height};
                projection_views[eye].subImage.imageArrayIndex = 0;
            }
            rendered = true;
        }
    }

    XrCompositionLayerProjection layer{XR_TYPE_COMPOSITION_LAYER_PROJECTION};
    layer.space = g.space;
    layer.viewCount = 2;
    layer.views = projection_views;
    const XrCompositionLayerBaseHeader* layers[] = {reinterpret_cast<XrCompositionLayerBaseHeader*>(&layer)};
    XrFrameEndInfo end_info{XR_TYPE_FRAME_END_INFO};
    end_info.displayTime = frame_state.predictedDisplayTime;
    end_info.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
    end_info.layerCount = rendered ? 1 : 0;
    end_info.layers = rendered ? layers : nullptr;
    xrEndFrame(g.session, &end_info);
    const int64_t display_ns = xr_time_to_monotonic_ns(frame_state.predictedDisplayTime);
    if (now_ns() > display_ns) g.late_submits++;     // submitted after its own display deadline
    if (rendered && !new_frame) g.stale_frames++;    // nothing new decoded: the previous image again
    if (g.submitted % 72 == 0) {
        LOGI("XRLOOP submitted=%lld late=%lld stale=%lld idle=%lld",
             static_cast<long long>(g.submitted.load()), static_cast<long long>(g.late_submits.load()),
             static_cast<long long>(g.stale_frames.load()), static_cast<long long>(g.dropped_no_frame.load()));
    }

    if (rendered && new_frame) {
        g.submitted++;
        std::lock_guard<std::mutex> lock(g.mutex);
        if (g.events.size() < 4096) {
            g.events.push_back({frame_pts_ns / 1000, xr_time_to_monotonic_ns(frame_state.predictedDisplayTime),
                                now_ns()});
        }
    }
}

}  // namespace

extern "C" {

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {
    g.vm = vm;
    return JNI_VERSION_1_6;
}

JNIEXPORT jboolean JNICALL Java_com_xrwired_receiverxr_NativeXr_start(JNIEnv* env, jclass, jobject activity) {
    if (g.running.exchange(true)) return JNI_FALSE;
    env->GetJavaVM(&g.vm);
    g.activity = env->NewGlobalRef(activity);
    if (!init_loader(env) || !init_egl() || !init_instance() || !init_session() || !g.renderer.init() ||
        !create_swapchains(g.want_ten_bit.load())) {
        g.running = false;
        return JNI_FALSE;
    }
    LOGI("XR session up; entering frame loop");
    while (g.running) {
        handle_events();
        serve_surface_request(env);
        if (g.session_running) {
            render_frame(env);
        } else {
            usleep(20000);
        }
    }
    g.renderer.destroy(env);
    if (g.session != XR_NULL_HANDLE) xrDestroySession(g.session);
    if (g.instance != XR_NULL_HANDLE) xrDestroyInstance(g.instance);
    g.session = XR_NULL_HANDLE;
    g.instance = XR_NULL_HANDLE;
    LOGI("XR session down after %lld submitted frames", static_cast<long long>(g.submitted.load()));
    return JNI_TRUE;
}

JNIEXPORT void JNICALL Java_com_xrwired_receiverxr_NativeXr_stop(JNIEnv*, jclass) { g.running = false; }

JNIEXPORT jobject JNICALL Java_com_xrwired_receiverxr_NativeXr_createDecoderSurface(JNIEnv* env, jclass,
                                                                                    jint width, jint height,
                                                                                    jboolean ten_bit) {
    std::unique_lock<std::mutex> lock(g.mutex);
    if (!g.running) return nullptr;
    g.surface_width = width;
    g.surface_height = height;
    g.surface_ten_bit = ten_bit == JNI_TRUE;
    g.surface_result = nullptr;
    g.surface_requested = true;
    g.surface_ready.wait_for(lock, std::chrono::seconds(10), [] { return !g.surface_requested; });
    return g.surface_result;
}

JNIEXPORT void JNICALL Java_com_xrwired_receiverxr_NativeXr_releaseDecoderSurface(JNIEnv* env, jclass) {
    g.renderer.release_decoder_surface(env);
}

/** Milliseconds before the predicted display time to hold each frame back (0 = submit immediately). */
/** Log XRPIX colour samples of the rendered image every few seconds (measurement runs). */
JNIEXPORT void JNICALL Java_com_xrwired_receiverxr_NativeXr_setPixelProbe(JNIEnv*, jclass, jboolean on) {
    g.probe_pixels = on == JNI_TRUE;
}

/** Ask for a 10-bit (linear) swapchain instead of sRGB. Must be called before start(). */
JNIEXPORT void JNICALL Java_com_xrwired_receiverxr_NativeXr_setTenBit(JNIEnv*, jclass, jboolean ten_bit) {
    g.want_ten_bit = ten_bit == JNI_TRUE;
}

JNIEXPORT void JNICALL Java_com_xrwired_receiverxr_NativeXr_setSubmitMargin(JNIEnv*, jclass, jint ms) {
    g.submit_margin_ms = ms;
    LOGI("submit margin = %d ms", ms);
}

/** Asks the runtime for a display refresh rate (XR_FB_display_refresh_rate); returns the rate in use. */
JNIEXPORT jfloat JNICALL Java_com_xrwired_receiverxr_NativeXr_setDisplayRate(JNIEnv*, jclass, jfloat hz) {
    if (!g.has_refresh_rate || g.session == XR_NULL_HANDLE) return 0.0f;
    PFN_xrEnumerateDisplayRefreshRatesFB enumerate = nullptr;
    PFN_xrRequestDisplayRefreshRateFB request = nullptr;
    PFN_xrGetDisplayRefreshRateFB get = nullptr;
    xrGetInstanceProcAddr(g.instance, "xrEnumerateDisplayRefreshRatesFB", reinterpret_cast<PFN_xrVoidFunction*>(&enumerate));
    xrGetInstanceProcAddr(g.instance, "xrRequestDisplayRefreshRateFB", reinterpret_cast<PFN_xrVoidFunction*>(&request));
    xrGetInstanceProcAddr(g.instance, "xrGetDisplayRefreshRateFB", reinterpret_cast<PFN_xrVoidFunction*>(&get));
    if (enumerate != nullptr) {
        uint32_t count = 0;
        enumerate(g.session, 0, &count, nullptr);
        std::vector<float> rates(count);
        enumerate(g.session, count, &count, rates.data());
        std::string text;
        for (float r : rates) text += std::to_string(r) + " ";
        LOGI("display refresh rates: %s", text.c_str());
    }
    if (hz > 0.0f && request != nullptr) check(request(g.session, hz), "xrRequestDisplayRefreshRateFB");
    float current = 0.0f;
    if (get != nullptr) get(g.session, &current);
    LOGI("display refresh rate now %.1f Hz", current);
    return current;
}

JNIEXPORT void JNICALL Java_com_xrwired_receiverxr_NativeXr_setStereoLayout(JNIEnv*, jclass, jint layout) {
    g.renderer.set_layout(layout == 1 ? StereoRenderer::SINGLE_VIEW : StereoRenderer::SIDE_BY_SIDE);
}

/** Submitted frames as flat triples (pts_us, predicted display time ns, submit time ns), then clears. */
JNIEXPORT jlongArray JNICALL Java_com_xrwired_receiverxr_NativeXr_drainDisplayEvents(JNIEnv* env, jclass) {
    std::vector<DisplayEvent> taken;
    {
        std::lock_guard<std::mutex> lock(g.mutex);
        taken.swap(g.events);
    }
    std::vector<jlong> flat;
    flat.reserve(taken.size() * 3);
    for (const DisplayEvent& e : taken) {
        flat.push_back(e.pts_us);
        flat.push_back(e.predicted_display_ns);
        flat.push_back(e.submitted_ns);
    }
    jlongArray array = env->NewLongArray(jsize(flat.size()));
    if (!flat.empty()) env->SetLongArrayRegion(array, 0, jsize(flat.size()), flat.data());
    return array;
}

/** Latest head pose for the PC: [id, qx,qy,qz,qw, px,py,pz, left fov l,r,u,d, right fov l,r,u,d]. */
JNIEXPORT jfloatArray JNICALL Java_com_xrwired_receiverxr_NativeXr_latestPose(JNIEnv* env, jclass) {
    HeadPose pose;
    {
        std::lock_guard<std::mutex> lock(g.pose_mutex);
        pose = g.pose;
    }
    float flat[22];
    flat[0] = float(pose.id);
    std::memcpy(flat + 1, pose.orientation, sizeof(pose.orientation));
    std::memcpy(flat + 5, pose.position, sizeof(pose.position));
    std::memcpy(flat + 8, pose.fov, sizeof(pose.fov));
    std::memcpy(flat + 16, pose.linear_velocity, sizeof(pose.linear_velocity));
    std::memcpy(flat + 19, pose.angular_velocity, sizeof(pose.angular_velocity));
    jfloatArray array = env->NewFloatArray(22);
    env->SetFloatArrayRegion(array, 0, 22, flat);
    return array;
}

/** The id of the latest pose, as a full-precision counter (the float array truncates above 2^24). */
/** Latest hand joints for the PC: [leftActive, rightActive, left 26*8 floats, right 26*8 floats]. */
JNIEXPORT jfloatArray JNICALL Java_com_xrwired_receiverxr_NativeXr_latestHands(JNIEnv* env, jclass) {
    const int per = kJoints * 8;
    float flat[2 + 2 * kJoints * 8];
    {
        std::lock_guard<std::mutex> lock(g.hands.mutex);
        flat[0] = g.hands.active[0] ? 1.0f : 0.0f;
        flat[1] = g.hands.active[1] ? 1.0f : 0.0f;
        std::memcpy(flat + 2, g.hands.joint[0], per * sizeof(float));
        std::memcpy(flat + 2 + per, g.hands.joint[1], per * sizeof(float));
    }
    jfloatArray array = env->NewFloatArray(2 + 2 * per);
    env->SetFloatArrayRegion(array, 0, 2 + 2 * per, flat);
    return array;
}

JNIEXPORT jlong JNICALL Java_com_xrwired_receiverxr_NativeXr_latestPoseId(JNIEnv*, jclass) {
    std::lock_guard<std::mutex> lock(g.pose_mutex);
    return jlong(g.pose.id);
}

JNIEXPORT jstring JNICALL Java_com_xrwired_receiverxr_NativeXr_stats(JNIEnv* env, jclass) {
    char text[256];
    std::snprintf(text, sizeof(text), "submitted=%lld decoded=%lld late=%lld stale=%lld eye=%dx%d depth=%s",
                  static_cast<long long>(g.submitted.load()), static_cast<long long>(g.renderer.frames()),
                  static_cast<long long>(g.late_submits.load()), static_cast<long long>(g.stale_frames.load()),
                  g.eyes[0].width, g.eyes[0].height, g.ten_bit_swapchain ? "10-bit" : "8-bit");
    return env->NewStringUTF(text);
}

}  // extern "C"
