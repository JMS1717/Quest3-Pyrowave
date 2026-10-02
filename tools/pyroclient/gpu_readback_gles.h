#pragma once
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace q3pw {
// Dedicated benchmark context, never the live OpenXR/WGPU context.
class ReadbackContext {
public:
    ReadbackContext() = default;
    ~ReadbackContext();
    ReadbackContext(const ReadbackContext &) = delete;
    ReadbackContext &operator=(const ReadbackContext &) = delete;
    bool initialize(std::string &error);
    EGLDisplay display() const { return display_; }
    EGLContext context() const { return context_; }
private:
    EGLDisplay display_ = EGL_NO_DISPLAY;
    EGLSurface surface_ = EGL_NO_SURFACE;
    EGLContext context_ = EGL_NO_CONTEXT;
    bool initialized_ = false;
};

bool has_extension(const char *list, const char *name);

// Caller owns the EGLImage and guarantees producer writes are complete and no
// further writes occur until return. Samples at 1:1, NEAREST, into linear RGBA8.
// Row zero samples texture v near zero (near one with flip_y). No implicit row
// reversal, sRGB transform, range adjustment, stereo split or client reprojection.
// Uses a dedicated ES3 context; intentionally does not restore caller GL state.
// Diagnostic-only hook after queued draws and before CPU completion/readback.
// If it permits producer reuse, it must first transfer a fence covering all reads.
using BeforeReadback = std::function<bool(EGLDisplay, std::string &)>;
bool readback_egl_image(EGLImageKHR image, uint32_t width, uint32_t height,
                        bool flip_y, std::vector<unsigned char> &rgba,
                        std::string &error, const BeforeReadback &before = {},
                        uint32_t draw_repeats = 1);
} // namespace q3pw
