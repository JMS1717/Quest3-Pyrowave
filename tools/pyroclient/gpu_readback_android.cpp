#include "gpu_readback_android.h"
#include "gpu_readback_gles.h"
#include <android/hardware_buffer.h>

namespace {
struct BufferReference {
    AHardwareBuffer *buffer;
    explicit BufferReference(AHardwareBuffer *b) : buffer(b) { AHardwareBuffer_acquire(buffer); }
    ~BufferReference() { AHardwareBuffer_release(buffer); }
};
struct ImageReference {
    EGLDisplay display;
    EGLImageKHR image;
    PFNEGLDESTROYIMAGEKHRPROC destroy;
    ~ImageReference() { destroy(display, image); }
};
}

bool readback_android_buffer(AHardwareBuffer *buffer, bool flip_y,
                              std::vector<unsigned char> &rgba, std::string &error,
                              const q3pw::BeforeReadback &before, uint32_t draw_repeats) {
    rgba.clear();
    error.clear();
    if (!buffer) { error = "No completed decoder buffer"; return false; }
    BufferReference retained(buffer);
    AHardwareBuffer_Desc description{};
    AHardwareBuffer_describe(buffer, &description);
    if (description.layers != 1 || (description.format != AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM &&
                                    description.format != AHARDWAREBUFFER_FORMAT_R8_UNORM) ||
        !(description.usage & AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE)) {
        error = "Expected a single-layer GPU-sampled RGBA8 or R8 buffer";
        return false;
    }
    q3pw::ReadbackContext context;
    if (!context.initialize(error)) return false;
    const char *extensions = eglQueryString(context.display(), EGL_EXTENSIONS);
    if (!q3pw::has_extension(extensions, "EGL_KHR_image_base") ||
        !q3pw::has_extension(extensions, "EGL_ANDROID_image_native_buffer") ||
        !q3pw::has_extension(extensions, "EGL_ANDROID_get_native_client_buffer")) {
        error = "Required Android EGL import extensions unavailable";
        return false;
    }
    auto get_native = reinterpret_cast<PFNEGLGETNATIVECLIENTBUFFERANDROIDPROC>(
        eglGetProcAddress("eglGetNativeClientBufferANDROID"));
    auto create = reinterpret_cast<PFNEGLCREATEIMAGEKHRPROC>(eglGetProcAddress("eglCreateImageKHR"));
    auto destroy = reinterpret_cast<PFNEGLDESTROYIMAGEKHRPROC>(eglGetProcAddress("eglDestroyImageKHR"));
    if (!get_native || !create || !destroy) {
        error = "Android EGL import entry points unavailable";
        return false;
    }
    const EGLClientBuffer native = get_native(buffer);
    if (!native) { error = "No native EGL client buffer"; return false; }
    const EGLint attributes[] = {EGL_IMAGE_PRESERVED_KHR, EGL_TRUE, EGL_NONE};
    const EGLImageKHR image = create(context.display(), EGL_NO_CONTEXT,
                                     EGL_NATIVE_BUFFER_ANDROID, native, attributes);
    if (image == EGL_NO_IMAGE_KHR) { error = "Android EGL image import failed"; return false; }
    ImageReference imported{context.display(), image, destroy};
    // readback finishes GLES work and removes its sibling texture before imported
    // image/context/reference destruction. No CPU flags or locks on the source.
    return q3pw::readback_egl_image(image, description.width, description.height,
                                   flip_y, rgba, error, before, draw_repeats);
}
